import os
from urllib.request import urlopen
with urlopen(os.environ["TOOLKIT_DEPLOY_URL"] + "/health/ready", timeout=15) as response:
    assert response.status == 200
