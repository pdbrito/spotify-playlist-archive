#!/usr/bin/env python3

import datetime
import logging
import os
import pathlib

from file_formatter import Formatter
from spotify import Spotify
from url import URL

logger: logging.Logger = logging.getLogger(__name__)


class FileUpdater:
    @classmethod
    async def update_files(cls, now: datetime.datetime, prod: bool) -> None:
        # Check nonempty to fail fast
        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        assert client_id and client_secret

        # Initialize the Spotify client
        access_token = await Spotify.get_access_token(client_id, client_secret)
        spotify = Spotify(access_token)
        try:
            await cls._update_files_impl(now, prod, spotify)
        finally:
            await spotify.shutdown()

    @classmethod
    async def _update_files_impl(
        cls, now: datetime.datetime, prod: bool, spotify: Spotify
    ) -> None:
        # Relative to project root
        playlists_dir = "playlists" if prod else "_playlists"
        aliases_dir = f"{playlists_dir}/aliases"
        plain_dir = f"{playlists_dir}/plain"
        pretty_dir = f"{playlists_dir}/pretty"
        cumulative_dir = f"{playlists_dir}/cumulative"

        # Ensure the directories exist
        for path in [
            aliases_dir,
            plain_dir,
            pretty_dir,
            cumulative_dir,
        ]:
            pathlib.Path(path).mkdir(parents=True, exist_ok=True)

        # Determine which playlists to scrape from the files in playlists/plain.
        # This makes it easy to add new a playlist: just touch an empty file like
        # playlists/plain/<playlist_id> and this script will handle the rest.
        playlist_ids = os.listdir(plain_dir)

        # Aliases are alternative playlists names. They're useful for avoiding
        # naming collisions when archiving personalized playlists, which have the
        # same name for every user. To add an alias, simply create a file like
        # playlists/aliases/<playlist_id> that contains the alternative name.
        aliases = {}
        for playlist_id in os.listdir(aliases_dir):
            alias_path = "{}/{}".format(aliases_dir, playlist_id)
            if playlist_id not in playlist_ids:
                logger.warning("Removing unused alias: {}".format(playlist_id))
                os.remove(alias_path)
                continue
            contents = open(alias_path).read().splitlines()
            if len(contents) != 1:
                logger.warning("Removing malformed alias: {}".format(playlist_id))
                os.remove(alias_path)
                continue
            aliases[playlist_id] = contents[0]

        readme_lines = []
        for playlist_id in playlist_ids:
            plain_path = "{}/{}".format(plain_dir, playlist_id)
            playlist = await spotify.get_playlist(playlist_id, aliases)
            readme_lines.append(
                "- [{}]({})".format(
                    playlist.name,
                    URL.pretty(playlist.name),
                )
            )

            pretty_path = "{}/{}.md".format(pretty_dir, playlist.name)
            cumulative_path = "{}/{}.md".format(cumulative_dir, playlist.name)

            for path in [plain_path, pretty_path, cumulative_path]:
                try:
                    prev_content = "".join(open(path).readlines())
                except Exception:
                    prev_content = ""

                if path == plain_path:
                    content = Formatter.plain(playlist_id, playlist)
                elif path == pretty_path:
                    content = Formatter.pretty(playlist_id, playlist)
                else:
                    content = Formatter.cumulative(
                        now, prev_content, playlist_id, playlist
                    )

                if content == prev_content:
                    logger.info("No changes to file: {}".format(path))
                else:
                    logger.info("Writing updates to file: {}".format(path))
                    with open(path, "w") as f:
                        f.write(content)

        # Sanity check: ensure same number of files in playlists/plain and
        # playlists/pretty - if not, some playlists have the same name and
        # overwrote each other in playlists/pretty OR a playlist ID was changed
        # and the file in playlists/plain was removed and needs to be re-added
        plain_playlists = set()
        for filename in os.listdir(plain_dir):
            with open(os.path.join(plain_dir, filename)) as f:
                plain_playlists.add(f.readline().strip())

        pretty_playlists = set()
        for filename in os.listdir(pretty_dir):
            pretty_playlists.add(filename[:-3])  # strip .md suffix

        missing_from_plain = pretty_playlists - plain_playlists
        missing_from_pretty = plain_playlists - pretty_playlists

        if missing_from_plain:
            raise Exception("Missing plain playlists: {}".format(missing_from_plain))

        if missing_from_pretty:
            raise Exception("Missing pretty playlists: {}".format(missing_from_pretty))

        # Lastly, update README.md
        if prod:
            readme = open("README.md").read().splitlines()
            index = readme.index("## Playlists")
            lines = (
                readme[: index + 1]
                + [""]
                + sorted(readme_lines, key=lambda line: line.lower())
            )
            with open("README.md", "w") as f:
                f.write("\n".join(lines) + "\n")
