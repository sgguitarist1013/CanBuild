"""Deploy the exact tested commit, wait for it to become live, then smoke test."""
import os
import time
import httpx
from smoke import check


def main():
    # Missing setup fails before sending any deployment request.
    token, service, url, sha = [os.environ[k] for k in
        ('RENDER_API_KEY', 'SERVICE_ID', 'SERVICE_URL', 'COMMIT_SHA')]
    if not all((token, service, url, sha)):
        raise RuntimeError('Release environment is incomplete')
    with httpx.Client(base_url='https://api.render.com/v1',
                      headers={'Authorization': f'Bearer {token}'}, timeout=30) as client:
        response = client.post(f'/services/{service}/deploys', json={'commitId': sha})
        response.raise_for_status()
        deploy_id = response.json()['id']
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            response = client.get(f'/services/{service}/deploys/{deploy_id}')
            response.raise_for_status()
            deploy = response.json()
            status = deploy['status']
            if status == 'live':
                if deploy.get('commit', {}).get('id') != sha:
                    raise RuntimeError('Live deployment does not match tested commit')
                check(url)
                return
            if status in {'build_failed', 'update_failed', 'canceled', 'deactivated', 'pre_deploy_failed'}:
                raise RuntimeError(f'Deployment failed: {status}')
            time.sleep(10)
    raise RuntimeError('Deployment did not become live before timeout')


if __name__ == '__main__':
    main()
