git fetch --all
git checkout origin/main
pip install -r requirements.txt
pkill gunicorn
sudo cp todoist2.conf /etc/nginx/conf.d/
sudo systemctl reload nginx
gunicorn -b 127.0.0.1:5000 web_app.__main__:app &