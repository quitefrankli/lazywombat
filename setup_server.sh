function run_client_side()
(
	set -exo pipefail

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
	set -exo pipefail

	function setup_python()
	{
		curl --fail --silent --show-error --location https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh
		export PATH="$HOME/.local/bin:$HOME/.deno/bin:/usr/local/bin:/usr/bin:/bin"
		uv sync --locked --no-dev --managed-python
		deno_version=$(.venv/bin/python -c 'from web_app.config import ConfigManager; print(ConfigManager().deno_version)')
		curl --fail --silent --show-error --location https://deno.land/install.sh | sh -s -- "$deno_version" --no-modify-path
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
	sudo apt install -y nginx ffmpeg nodejs npm redis-server curl unzip
	sudo npm install -g @rynfar/meridian @anthropic-ai/claude-code
	setup_python
	setup_certs
	sudo systemctl start nginx
	sudo systemctl enable nginx
	# sudo systemctl status nginx

	bash update_server.sh
)