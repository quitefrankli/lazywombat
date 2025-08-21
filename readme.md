# Web App

## Running

```bash
pip install -r requirements.txt
python -m web_app [--debug] [--port PORT]
```

## Testing

run `python -m web_app --debug --port 12345`
and on separate terminal run `pytest`

need to install:
* sudo apt-get install ffmpeg

## On EC2

```
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh
bash ~/miniconda -b -p $HOME/miniconda
~/miniconda/bin/conda init bash
source ~/.bashrc

sudo yum install nginx ffmpeg

# for generating certs for ssl
conda install certbot -y
export DOMAIN=lazywombat.site
export EMAIL=your email
sudo $(which certbot) certonly --standalone -d $DOMAIN --staple-ocsp -m $EMAIL --agree-tos
sudo cp lazywombat.conf /etc/nginx/conf.d/

sudo systemctl start nginx
sudo systemctl enable nginx
# below step might be needed but not sure
# sudo systemctl reload nginx

sudo systemctl status nginx

gunicorn -b 127.0.0.1:5000 web_app.__main__:app &
```

## Updating Server

### Method 1: via git push

Simply push from main branch, force push also works too

### Method 2: via curl

1. make sure you are on main branch (force pushes are NOT supported, make sure origin/main is STRICTLY behind main)
2. `PATCH=$(git format-patch origin/main..main --stdout | gzip -c | base64 -w 0) && curl -F "username=$USER" -F "password=$PASS" -F "patch=$PATCH" https://lazywombat.site/api/update`

## Creating Backup

`curl -F "username=$USERNAME" "password=$PASSWORD" https://lazywombat.site/api/backup`