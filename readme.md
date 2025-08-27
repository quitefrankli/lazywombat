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

## Cloud Setup

1. install terraform
2. `terraform -chdir terraform init`
3. `terraform -chdir terraform plan`
4. `terraform -chdir terraform apply -auto-approve`

terraform should output something like

> elastic_ip = "54.79.119.112"

with the generated ip, the "A Record" would need to be updated via the appropriate domain provider

### Setup EC2

```bash
export ELASTIC_IP=$(terraform -chdir=terraform output elastic_ip | sed 's/\"//g')

ssh ubuntu@$ELASTIC_IP -t "sudo useradd -m $USER && sudo adduser $USER sudo && sudo cp -r ~/.ssh /home/$USER/ && sudo chown -R $USER:$USER /home/ereh && sudo chsh $USER -s /bin/bash && echo \"$USER ALL=(ALL) NOPASSWD: ALL\" | sudo tee -a /etc/sudoers"

# assuming ssh key has been added to GitHub already
scp ~/.ssh/id_rsa.pub $ELASTIC_IP:~/.ssh/
scp ~/.ssh/id_rsa $ELASTIC_IP:~/.ssh/
```

```bash
ssh $ELASTIC_IP
git clone git@github.com:quitefrankli/lazywombat.git
cd lazywombat
bash lazywombat/setup_server.sh
```

## Updating Server

### Method 1: via git push

Simply push from main branch, force push also works too

### Method 2: via curl

1. make sure you are on main branch (force pushes are NOT supported, make sure origin/main is STRICTLY behind main)
2. `PATCH=$(git format-patch origin/main..main --stdout | gzip -c | base64 -w 0) && curl -F "username=$USER" -F "password=$PASS" -F "patch=$PATCH" https://lazywombat.site/api/update`

## Creating Backup

`curl -F "username=$USERNAME" "password=$PASSWORD" https://lazywombat.site/api/backup`