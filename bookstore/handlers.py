"""Handlers for Bookstore API"""
import json

import aiobotocore
from notebook.base.handlers import APIHandler
from notebook.base.handlers import path_regex
from notebook.utils import url_path_join
from tornado import web

from ._version import __version__
from .bookstore_config import BookstoreSettings
from .bookstore_config import validate_bookstore
from .s3_paths import s3_path
from .s3_paths import s3_key
from .s3_paths import s3_display_path


version = __version__


class BookstoreVersionHandler(APIHandler):
    """Handler responsible for Bookstore version information

    Used to lay foundations for the bookstore package. Though, frontends can use this endpoint for feature detection.
    """

    @web.authenticated
    def get(self):
        self.finish(
            json.dumps(
                {
                    "bookstore": True,
                    "version": self.settings['bookstore']["version"],
                    "validation": self.settings['bookstore']["validation"],
                }
            )
        )


# TODO: Add a check. Note: We need to ensure that publishing is not configured if bookstore settings are not
#       set. Because of how the APIHandlers cannot be configurable, all we can do is reach into settings
#       For applications this will mean checking the config and then applying it in


class BookstorePublishHandler(APIHandler):
    """Publish a notebook to the publish path"""

    def initialize(self):
        """Initialize a helper to get bookstore settings and session information quickly"""
        self.bookstore_settings = BookstoreSettings(config=self.config)
        self.session = aiobotocore.get_session()

    @web.authenticated
    async def put(self, path=''):
        """Publish a notebook on a given path.

        The payload directly matches the contents API for PUT.
        """
        self.log.info("Attempt publishing to %s", path)

        if path == '' or path == '/':
            raise web.HTTPError(400, "Must provide a path for publishing")

        model = self.get_json_body()
        if model:
            await self._publish(model, path.lstrip('/'))
        else:
            raise web.HTTPError(400, "Cannot publish an empty model")

    async def _publish(self, model, path):
        """Publish notebook model to the path"""
        if model['type'] != 'notebook':
            raise web.HTTPError(400, "bookstore only publishes notebooks")
        content = model['content']

        full_s3_path = s3_path(
            self.bookstore_settings.s3_bucket, self.bookstore_settings.published_prefix, path
        )
        file_key = s3_key(self.bookstore_settings.published_prefix, path)

        self.log.info(
            "Publishing to %s",
            s3_display_path(
                self.bookstore_settings.s3_bucket, self.bookstore_settings.published_prefix, path
            ),
        )

        async with self.session.create_client(
            's3',
            aws_secret_access_key=self.bookstore_settings.s3_secret_access_key,
            aws_access_key_id=self.bookstore_settings.s3_access_key_id,
            endpoint_url=self.bookstore_settings.s3_endpoint_url,
            region_name=self.bookstore_settings.s3_region_name,
        ) as client:
            self.log.info("Processing published write of %s", path)
            obj = await client.put_object(
                Bucket=self.bookstore_settings.s3_bucket, Key=file_key, Body=json.dumps(content)
            )
            self.log.info("Done with published write of %s", path)

        self.set_status(201)

        resp_content = {"s3path": full_s3_path}

        if 'VersionId' in obj:
            resp_content["versionID"] = obj['VersionId']

        resp_str = json.dumps(resp_content)
        self.finish(resp_str)


def load_jupyter_server_extension(nb_app):
    web_app = nb_app.web_app
    host_pattern = '.*$'

    # Always enable the version handler
    base_bookstore_pattern = url_path_join(web_app.settings['base_url'], '/api/bookstore')
    web_app.add_handlers(host_pattern, [(base_bookstore_pattern, BookstoreVersionHandler)])
    bookstore_settings = BookstoreSettings(parent=nb_app)
    web_app.settings['bookstore'] = {
        "version": version,
        "validation": validate_bookstore(bookstore_settings),
    }

    check_published = [
        web_app.settings['bookstore']['validation'].get("bookstore_valid"),
        web_app.settings['bookstore']['validation'].get("publish_valid"),
    ]

    if not all(check_published):
        nb_app.log.info("[bookstore] Not enabling bookstore publishing, endpoint not configured")
    else:
        nb_app.log.info(f"[bookstore] Enabling bookstore publishing, version: {version}")
        web_app.add_handlers(
            host_pattern,
            [
                (
                    url_path_join(base_bookstore_pattern, r"/published%s" % path_regex),
                    BookstorePublishHandler,
                )
            ],
        )
