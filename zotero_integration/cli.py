import click
from pyzotero import zotero

@click.command()
def cli():
    """Display entries from your local Zotero database."""
    # Initialize Zotero with local=True for local database access
    zot = zotero.Zotero(None, 'user', None, local=True)
    
    try:
        # Get all items from local database
        items = zot.top()
        
        if not items:
            click.echo("No items found in local Zotero database.")
            return
            
        # Display each item's details
        for item in items:
            click.echo(f"Item: {item['data']['itemType']} | Key: {item['data']['key']}")
            
    except Exception as e:
        click.echo(f"Error accessing Zotero database: {str(e)}")


if __name__ == "__main__":
    cli()
