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
sudo systemctl start nginx
sudo systemctl enable nginx
sudo systemctl status nginx

sudo cp todoist2.conf /etc/nginx/confi.d/
sudo systemctl reload nginx

gunicorn -b 127.0.0.1:5000 web_app.__main__:app &
```