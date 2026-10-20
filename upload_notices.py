"""Upload the collected notice list to the Cafe24 site over FTP."""
import os
import sys
from ftplib import FTP
from pathlib import Path


def main():
    host = os.environ.get('CAFE24_FTP_HOST', '').strip()
    user = os.environ.get('CAFE24_FTP_USER', '').strip()
    password = os.environ.get('CAFE24_FTP_PASSWORD', '')
    remote_dir = os.environ.get('CAFE24_FTP_DIR', 'www').strip() or 'www'
    port = int(os.environ.get('CAFE24_FTP_PORT', '21'))
    local = Path(os.environ.get('CHUNGNAM_NOTICES_JSON', 'chungnam_support_notices.json'))
    if not host or not user or not password:
        print('Cafe24 FTP secrets are missing.', file=sys.stderr)
        return 2
    if not local.is_file():
        print('Notice list was not created.', file=sys.stderr)
        return 2
    ftp = FTP(timeout=60)
    ftp.connect(host, port)
    ftp.login(user, password)
    ftp.set_pasv(True)
    if remote_dir not in ('/', '.'):
        ftp.cwd(remote_dir)
    with local.open('rb') as handle:
        ftp.storbinary('STOR chungnam_support_notices.json', handle)
    ftp.quit()
    print('uploaded chungnam_support_notices.json', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
