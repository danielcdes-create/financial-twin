"""Local administrator recovery: no password in command arguments, logs or chat."""
import getpass
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from pymongo import MongoClient
from app.config import get_settings
from app.main import configured_mongodb_uri
from app.auth import OWNER_ID, hash_password, owner


def main():
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError('Banco não configurado.')
    with MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=5000) as client:
        db = client[settings.mongodb_database]
        account = owner(db)
        if not account:
            raise RuntimeError('Configure a conta no primeiro acesso antes de recuperar a senha.')
        password = getpass.getpass('Nova senha (mínimo de 12 caracteres): ')
        confirmation = getpass.getpass('Repita a nova senha: ')
        if not 12 <= len(password) <= 256 or password != confirmation:
            raise RuntimeError('As senhas devem coincidir e ter entre 12 e 256 caracteres.')
        result = db.account.update_one({'_id': OWNER_ID, 'auth_version': account['auth_version']},
            {'$set': {'password_hash': hash_password(password), 'sessions': []}, '$inc': {'auth_version': 1}})
        if result.matched_count != 1:
            raise RuntimeError('A conta mudou durante a recuperação. Tente novamente.')
        print('Senha redefinida. Todas as sessões anteriores foram encerradas.')


if __name__ == '__main__': main()
