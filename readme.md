# Web App

## Setup Env

conda create -n todoist2 python=3.11
conda activate todoist2
pip install -r requirements.txt
python -m web_app [--debug]

## On EC2

```
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh
bash ~/miniconda -b -p $HOME/miniconda
~/miniconda/bin/conda init bash
source ~/.bashrc

sudo yum install nginx

# for generating certs for ssl
conda install certbot -y
export DOMAIN=your website domain
export EMAIL=your email
sudo $(which certbot) certonly --standalone -d $DOMAIN --staple-ocsp -m $EMAIL --agree-tos
sudo cp todoist2.conf /etc/nginx/conf.d/
sudo sed -i 's/yourdomain\.com/'"$DOMAIN"'/g' /etc/nginx/conf.d/todoist2.conf

sudo systemctl start nginx
sudo systemctl enable nginx
# below step might be needed but not sure
# sudo systemctl reload nginx

sudo systemctl status nginx

gunicorn -b 127.0.0.1:5000 web_app.__main__:app &
```