"""Create server-only signing/pairing secrets, without printing their values."""
import re
import secrets
from pathlib import Path


def configure(path: Path):
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    changed = []
    for key in ('JWT_SECRET', 'ACCOUNT_SETUP_TOKEN'):
        pattern = re.compile(r'^' + key + r'=(.*)$', re.MULTILINE)
        match = pattern.search(content)
        value = match.group(1).strip().strip('\"\'') if match else ''
        if len(value) >= 32:
            continue
        entry = key + '=' + secrets.token_urlsafe(48)
        content = pattern.sub(lambda _: entry, content) if match else content.rstrip() + '\n' + entry + '\n'
        changed.append(key)
    if changed:
        path.write_text(content, encoding='utf-8')
    return changed


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[2] / '.env'
    changed = configure(target)
    print('Segurança de conta: ' + (', '.join(changed) + ' configurados no .env privado.' if changed else 'já configurada.'))
    print('No primeiro acesso, use ACCOUNT_SETUP_TOKEN do .env como código de configuração. Não compartilhe este código.')
