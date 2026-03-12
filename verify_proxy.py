import os, sys
sys.path.insert(0, r'd:\APMD_Eoffice_Bot')
from modules import utils
print('network loaded:', utils.CONFIG.get('network'))
print('HTTP_PROXY', os.environ.get('HTTP_PROXY'))
print('proxies helper', utils.get_requests_proxies())
try:
    print('proxy test', utils.test_proxy_connection())
except Exception as e:
    print('proxy error', e)
