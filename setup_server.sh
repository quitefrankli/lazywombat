set -ex

function setup_conda()
{
	miniforge_url=https://github.com/conda-forge/miniforge/releases/latest/download/
	installer=Miniforge3-Linux-x86_64.sh

	wget ${miniforge_url}${installer}

	bash $installer -b
	rm $installer

	echo "source $HOME/miniforge3/bin/activate" >> ~/.bashrc
	source ~/.bashrc
}

function setup_certs()
{
	DOMAIN=lazywombat.site
	EMAIL="erehnimda@gmail.com"
	
	mamba install -y anaconda::cryptography
	mamba install -y certbot
	sudo $(which certbot) certonly --standalone -d $DOMAIN --staple-ocsp -m $EMAIL --agree-tos
	sudo cp lazywombat.conf /etc/nginx/conf.d/
}

sudo apt update
sudo apt install -y nginx gunicorn # ffmpeg
setup_conda
setup_certs
sudo systemctl start nginx
sudo systemctl enable nginx
# sudo systemctl status nginx

bash update_server.sh