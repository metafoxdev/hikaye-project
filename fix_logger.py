import os
import glob

routes = glob.glob('routes/*_routes.py') + ['routes/utils.py']
for r in routes:
    with open(r, 'r') as f:
        content = f.read()
    
    if 'import logging' not in content:
        content = 'import logging\nlogger = logging.getLogger("hikaye_resimleyici")\n' + content
        with open(r, 'w') as f:
            f.write(content)
