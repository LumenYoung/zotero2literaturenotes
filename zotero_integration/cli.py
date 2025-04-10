import click
import httpx
import re
import os
from pathlib import Path
from pyfzf import FzfPrompt
from pyzotero import zotero
from datetime import datetime, timezone

# Unset HTTP proxies
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)


class ZoteroItem:
    def __init__(self, raw_item):
        self.raw_item = raw_item
        self.data = raw_item["data"]
        self.metadata = self._parse_extra(self.data.get("extra", ""))

    def _parse_extra(self, extra_text):
        """Parse the extra field into a dictionary of metadata"""
        if not extra_text:
            return {}
        return dict(
            tuple(line.split(":", 1)) for line in extra_text.split("\n") if ":" in line
        )

    def get_authors(self):
        creators = self.data.get("creators", [])
        authors = []
        for creator in creators:
            if creator["creatorType"] == "author":
                if "firstName" in creator and "lastName" in creator:
                    authors.append(f"{creator['firstName']} {creator['lastName']}")
                elif "name" in creator:
                    authors.append(creator["name"])
        return authors

    def get_tags(self):
        tags = [t["tag"] for t in self.data.get("tags", [])]
        tags.append("literature")
        return tags

    def get_short_title(self):
        """Create a short version of the title using first five words"""
        title = self.data.get("title", "Untitled")  # Use "Untitled" as fallback
        words = title.split()[:5]
        return " ".join(words)

    def get_frontmatter(self):
        frontmatter = {}

        # Add citation key if exists
        if citation_key := self.metadata.get("Citation Key"):
            frontmatter["citation_key"] = citation_key
            frontmatter["aliases"] = [citation_key]
        else:
            # Use short title as alias if no citation key exists
            frontmatter["aliases"] = [self.get_short_title()]

        # Add mapped fields
        for source_path, target_key in FRONTMATTER_MAPPING.items():
            if value := get_nested_value(self.raw_item, source_path):
                if target_key == "authors":
                    frontmatter["authors"] = self.get_authors()
                elif target_key == "tags":
                    frontmatter["tags"] = self.get_tags()
                else:
                    frontmatter[target_key] = value

        return frontmatter

    def _sanitize_frontmatter_value(self, value):
        """Sanitize string values for frontmatter by handling quotes properly"""
        if isinstance(value, str):
            # Remove backslashes first
            value = value.replace('\\', '')
            if '"' in value:
                # If value contains double quotes, use single quotes
                return f"'{value}'"
            # Default to double quotes
            return f'"{value}"'
        return value

    def create_markdown(self):
        frontmatter = ["---"]
        for key, value in self.get_frontmatter().items():
            if isinstance(value, (list, tuple)):
                frontmatter.append(f"{key}: {value}")
            else:
                sanitized_value = self._sanitize_frontmatter_value(value)
                frontmatter.append(f"{key}: {sanitized_value}")
        title = self.data.get("title", "Untitled")  # Use "Untitled" as fallback
        frontmatter.extend(["---", "", f"# {title}", ""])
        return "\n".join(frontmatter)


def sanitize_filename(title):
    """Convert title to valid filename following strict naming rules"""
    if not title:
        raise ValueError("Page name cannot be empty")
    
    # Replace colons with space-dash
    filename = title.replace(":", " -")
    # Remove problematic characters
    filename = re.sub(r'[@$!<>"/\\|?*]', "", filename)
    # Remove multiple dashes
    filename = re.sub(r"-+", "-", filename)
    # Remove leading/trailing dashes and spaces
    filename = filename.strip("- ")
    
    # Remove . or ^ from start of filename instead of raising error
    while filename.startswith(".") or filename.startswith("^"):
        filename = filename[1:]
    
    # Check if the name (without .md) ends with any extension-like pattern
    name_without_md = filename[:-3] if filename.endswith(".md") else filename
    if re.search(r'\.[a-zA-Z0-9]+$', name_without_md):
        print(f"Warning: '{name_without_md}' contains what appears to be a file extension")
    
    return f"{filename}.md"


# Mapping from Zotero fields to frontmatter keys
FRONTMATTER_MAPPING = {
    "key": "zotero_key",
    "data.itemType": "item_type",
    "data.title": "title",
    "data.abstractNote": "abstract",
    "data.url": "url",
    "data.DOI": "doi",
    "data.publicationTitle": "publication",
    "data.dateAdded": "add_date",
    "data.creators": "authors",
    "data.tags": "tags",
}


def get_nested_value(item, path):
    """Get value from nested dictionary using dot notation path"""
    current = item
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def is_added_today(date_str):
    """Check if the given date string is from today"""
    if not date_str:
        return False

    item_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    today = datetime.now(timezone.utc)

    return (
        item_date.year == today.year
        and item_date.month == today.month
        and item_date.day == today.day
    )


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        elif len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Too many matches: {', '.join(sorted(matches))}")

    def resolve_command(self, ctx, args):
        # always return the full command name
        _, cmd, args = super().resolve_command(ctx, args)
        return cmd.name, cmd, args


@click.group(invoke_without_command=True, cls=AliasedGroup)
@click.pass_context
def cli(ctx):
    """Synchronize Zotero literature with your knowledge vault"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(today)


@cli.command()
def today():
    """Create markdown files for items added today"""
    zot = zotero.Zotero(library_id=0, library_type="user", api_key="", local=True)
    zot.add_parameters(limit=50, sort="dateAdded", direction="desc")
    try:
        items = zot.items()
    except httpx.ConnectError:
        print("Error: Could not connect to Zotero. Please make sure Zotero is running.")
        return

    # Ensure the Literature_Notes directory exists
    notes_dir = Path.home() / "Documents" / "Silverbullet" / "Literature_Note"
    notes_dir.mkdir(parents=True, exist_ok=True)

    for raw_item in items:
        # Skip attachments
        if raw_item["data"]["itemType"] == "attachment":
            continue

        item = ZoteroItem(raw_item)
        # Skip items without titles
        if not item.data.get("title"):
            continue

        if is_added_today(item.data["dateAdded"]):
            # Create filename from title
            filename = sanitize_filename(item.data["title"])
            filepath = notes_dir / filename

            # Only create file if it doesn't exist
            if filepath.exists():
                continue

            # Write to file
            filepath.write_text(item.create_markdown())
            print(f"Created: {filepath}")


@cli.command(name="search")
def search():
    """Search through Zotero items using fzf"""
    zot = zotero.Zotero(library_id=0, library_type="user", api_key="", local=True)
    
    # Get total number of items and use that as the limit
    total_items = zot.count_items()
    zot.add_parameters(limit=total_items, sort="dateAdded", direction="desc")

    try:
        items = zot.items()
    except httpx.ConnectError:
        print("Error: Could not connect to Zotero. Please make sure Zotero is running.")
        return

    # Filter out attachments and prepare titles for fzf
    titles = []
    title_to_item = {}
    for raw_item in items:
        if raw_item["data"]["itemType"] == "attachment":
            continue
        item = ZoteroItem(raw_item)
        if not item.data.get("title"):
            continue
        titles.append(item.data["title"])
        title_to_item[item.data["title"]] = item

    # Use fzf to select a title
    fzf = FzfPrompt()
    try:
        selected = fzf.prompt(titles)[0]

        # Get the selected item
        item = title_to_item[selected]

        # Create filename and path
        filename = sanitize_filename(selected)
        notes_dir = Path.home() / "Documents" / "Silverbullet" / "Literature_Note"
        filepath = notes_dir / filename

        # Write to file
        filepath.write_text(item.create_markdown())
        print(f"Created: {filepath}")

    except (IndexError, KeyError):
        print("No selection made")


if __name__ == "__main__":
    cli()
