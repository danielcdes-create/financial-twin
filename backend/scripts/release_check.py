"""Read-only financial fingerprint and HTTP release checks. Does not run startup."""
import sys, os, json, hashlib, argparse
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import json_util
from pymongo import MongoClient
from app.config import get_settings
from app.main import configured_mongodb_uri, android_apk_path
from app.release_manifest import verified_release

def fingerprint():
    settings = get_settings(); result = {}
    with MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000) as client:
        db = client[settings.mongodb_database]
        for name in ('transactions', 'obligations', 'credit_card_bills', 'installment_plans', 'accounts', 'commitments', 'commitment_amount_rules', 'account_profiles'):
            digest = hashlib.sha256(); count = 0
            for row in db[name].find({}).sort('_id', 1):
                digest.update(json_util.dumps(row, sort_keys=True, json_options=json_util.CANONICAL_JSON_OPTIONS).encode())
                count += 1
            result[name] = {'count': count, 'sha256': digest.hexdigest()}
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['before', 'after'])
    parser.add_argument('folder', type=Path)
    parser.add_argument('--expected-version-code', type=int)
    parser.add_argument('--expected-version-name')
    args = parser.parse_args()
    folder = args.folder; folder.mkdir(parents=True, exist_ok=True)
    if args.phase == 'before':
        before = fingerprint()
        (folder / 'production-before.json').write_text(json.dumps(before, indent=2), encoding='utf8')
        print('Read-only baseline fingerprint stored')
    else:
        previous = json.loads((folder / 'production-before.json').read_text(encoding='utf8'))
        current = fingerprint()
        assert current == previous, 'Financial source documents changed; investigate before claiming preservation'
        auth_token = os.getenv('RELEASE_CHECK_TOKEN', '').strip()
        def get(path, token=''):
            headers = {'Authorization': f'Bearer {token}'} if token else {}
            req = UrlRequest('http://127.0.0.1:8000' + path, headers=headers)
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        health = get('/health')
        expected = verified_release(android_apk_path())
        assert expected['available'], 'Local APK/manifest does not verify'
        if auth_token:
            release = get('/v1/app-update', token=auth_token)
            for field in ('version_code', 'version_name', 'sha256', 'size_bytes'):
                assert release.get(field) == expected[field], f'Published {field} differs from verified manifest'
        else:
            release = expected
        if args.expected_version_code is not None:
            assert release['version_code'] == args.expected_version_code
        if args.expected_version_name is not None:
            assert release['version_name'] == args.expected_version_name
        assert health['mongodb'] == 'connected'
        assert health['auth_required'] is True
        protected = {}
        for path in ('/v1/app-update', '/v1/app-update/apk', '/v1/movements', '/v1/commitments', '/v1/insights/closure?start=2026-09-01&end=2026-09-29'):
            try:
                req = UrlRequest('http://127.0.0.1:8000' + path)
                with urlopen(req, timeout=30) as response:
                    response.read(64)
            except HTTPError as error:
                protected[path] = error.code
            else:
                raise AssertionError('Protected route accepted unauthenticated request: ' + path)
            assert protected[path] == 401
        is_prod = get_settings().environment.lower() == 'production'
        insights_paths = None
        voice_captured_at_in_schema = None
        openapi_disabled = False
        if is_prod:
            try:
                get('/openapi.json')
            except HTTPError as error:
                assert error.code == 404
                openapi_disabled = True
            else:
                raise AssertionError('OpenAPI schema must be disabled in production')
        else:
            schema = get('/openapi.json')
            assert '/v1/insights/simulate' in schema['paths']
            multipart = schema['paths']['/v1/documents/extract']['post']['requestBody']['content']['multipart/form-data']['schema']['$ref'].split('/')[-1]
            assert 'captured_at' in schema['components']['schemas'][multipart]['properties']
            insights_paths = len([p for p in schema['paths'] if p.startswith('/v1/insights')])
            voice_captured_at_in_schema = True
        result = {
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'financial_documents_unchanged': True,
            'financial_fingerprints': current,
            'health': health,
            'release': release,
            'openapi_disabled': openapi_disabled,
            'insights_paths': insights_paths,
            'protected_routes': protected,
            'voice_captured_at_in_schema': voice_captured_at_in_schema,
        }
        (folder / 'live-release-results.json').write_text(json.dumps(result, indent=2), encoding='utf8')
        print(json.dumps(result, indent=2))
