function run_client_side()
(
	set -ex

	# $1 - cloud provider (aws, oci)
	if [ -z "$1" ]
	then
		echo "cloud provider not specified"
		exit 1
	fi

	# deploy infrastructure
	CLOUD_PROVIDER=$1
	terraform -chdir=terraform/$CLOUD_PROVIDER init
	terraform -chdir=terraform/$CLOUD_PROVIDER plan
	terraform -chdir=terraform/$CLOUD_PROVIDER apply -auto-approve
	export SERVER_IP_ADDR=$(terraform -chdir=terraform/$CLOUD_PROVIDER output server_ip_addr | sed 's/\"//g')

	echo "Waiting for server to come online..."
	until ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new ubuntu@$SERVER_IP_ADDR true 2>/dev/null; do
		sleep 5
	done

	# setup user on server
	ssh ubuntu@$SERVER_IP_ADDR -t "sudo useradd -m $USER && sudo adduser $USER sudo && sudo cp -r ~/.ssh /home/$USER/ && sudo chown -R $USER:$USER /home/$USER && sudo chsh $USER -s /bin/bash && echo \"$USER ALL=(ALL) NOPASSWD: ALL\" | sudo tee -a /etc/sudoers"
	# web_app needs to be able to push to github, so we need to sync ssh key across
	scp ~/.ssh/id_rsa* $SERVER_IP_ADDR:~/.ssh/
	# assuming the local .env file is appropriately populated
	scp .env $SERVER_IP_ADDR:~/.env

)

function run_server_side()
(
	set -ex

	function setup_conda()
	{
		miniforge_url=https://github.com/conda-forge/miniforge/releases/latest/download/
		arch=$(uname -m)
		if [ "$arch" = "aarch64" ]
		then
			installer=Miniforge3-Linux-aarch64.sh
		else
			installer=Miniforge3-Linux-x86_64.sh
		fi

		wget ${miniforge_url}${installer}

		bash $installer -b
		rm $installer

		echo "source $HOME/miniforge3/bin/activate" >> ~/.bashrc
	}

	function setup_certs()
	{
		DOMAIN=nabicat.site
		EMAIL="${CERTBOT_EMAIL:?CERTBOT_EMAIL is required}"
		
		# OCI Ubuntu images ship with iptables rules that block incoming traffic at the OS level (separate from the security list)
	  	sudo apt install -y iptables-persistent
		sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
		sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
		sudo netfilter-persistent save

		# apt's certbot ships a systemd timer (certbot.timer) that auto-renews twice daily.
		# Pre/post hooks are persisted into /etc/letsencrypt/renewal/<domain>.conf so
		# `certbot renew` cycles nginx automatically on each renewal.
		sudo apt install -y certbot
		sudo systemctl stop nginx
		sudo certbot certonly --standalone -d $DOMAIN --staple-ocsp -m $EMAIL --agree-tos \
			--pre-hook "systemctl stop nginx" \
			--post-hook "systemctl start nginx"
		sudo cp nabicat.conf /etc/nginx/conf.d/
	}

	sudo apt update
	sudo apt install -y nginx gunicorn ffmpeg nodejs npm redis-server
	sudo npm install -g @rynfar/meridian @anthropic-ai/claude-code
	setup_conda
	source "$HOME/miniforge3/bin/activate"
	mamba install -y deno
	setup_certs
	sudo systemctl start nginx
	sudo systemctl enable nginx
	# sudo systemctl status nginx
	mamba clean -a -y # frees up some space

	bash update_server.sh
)