set -ex

git checkout main
git fetch
git reset --hard origin/main

# Default values
PATCHES=""
while getopts "p" opt; do
    case "$opt" in
        p)
            PATCHES="$OPTARG"
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done
shift $((OPTIND-1))

# check if patches are provided
if [ -n "$PATCHES" ]
then
    git am "$PATCHES"
    git push
fi

pip install -r requirements.txt --quiet
pkill gunicorn
sudo cp lazywombat.conf /etc/nginx/conf.d/
sudo systemctl reload nginx
gunicorn -b 127.0.0.1:5000 web_app.__main__:app &