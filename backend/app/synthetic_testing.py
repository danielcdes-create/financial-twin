"""Keep synthetic integration databases off the financial cluster by default."""
import os
import re


def require_synthetic_target(uri: str):
    loopback = re.match(
        r'^mongodb://(?:[^@/]+@)?(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/|\?|$)',
        uri, re.IGNORECASE,
    )
    if not loopback and os.environ.get('FIN_ALLOW_REMOTE_TEST_DATABASE') != '1':
        raise RuntimeError(
            'Testes sintéticos exigem MongoDB local via MONGODB_URI. '
            'Bancos de teste remotos também consomem a capacidade do cluster financeiro. '
            'Para um teste remoto deliberado, defina FIN_ALLOW_REMOTE_TEST_DATABASE=1.'
        )
