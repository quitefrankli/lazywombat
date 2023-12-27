import click

from .desktop_app import DesktopApp


@click.command()
@click.option("--debug/--no-debug", default=False)
def main(debug: bool):
	app = DesktopApp(debug=debug)
	app.run()

if __name__ == "__main__":
	main()

