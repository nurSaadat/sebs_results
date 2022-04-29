import json
import os
import secrets
import uuid
from typing import List, Optional

import docker
import minio

from sebs.cache import Cache
from sebs.types import Storage as StorageTypes
from sebs.faas.storage import PersistentStorage


class Minio(PersistentStorage):
    @staticmethod
    def typename() -> str:
        return f"{Minio.deployment_name()}.Minio"

    @staticmethod
    def deployment_name() -> str:
        return "minio"

    # the location does not matter
    MINIO_REGION = "us-east-1"

    def __init__(self, docker_client: docker.client, cache_client: Cache, replace_existing: bool):
        super().__init__(self.MINIO_REGION, cache_client, replace_existing)
        self._docker_client = docker_client
        self._storage_container: Optional[docker.container] = None

    @staticmethod
    def _define_http_client():
        """
            Minio does not allow another way of configuring timeout for connection.
            The rest of configuration is copied from source code of Minio.
        """
        import urllib3
        from datetime import timedelta
        timeout = timedelta(seconds=1).seconds

        return urllib3.PoolManager(
            timeout=urllib3.util.Timeout(connect=timeout, read=timeout),
            maxsize=10,
            retries=urllib3.Retry(total=5,backoff_factor=0.2,status_forcelist=[500, 502, 503, 504])
        )

    def start(self, port: int = 9000):
        self._port = port
        self._access_key = secrets.token_urlsafe(32)
        self._secret_key = secrets.token_hex(32)
        self._url = ""
        self.logging.info("Minio storage ACCESS_KEY={}".format(self._access_key))
        self.logging.info("Minio storage SECRET_KEY={}".format(self._secret_key))
        try:
            self._storage_container = self._docker_client.containers.run(
                "minio/minio:latest",
                command="server /data",
                network_mode="bridge",
                ports={"9000": str(self._port)},
                environment={
                    "MINIO_ACCESS_KEY": self._access_key,
                    "MINIO_SECRET_KEY": self._secret_key,
                },
                remove=True,
                stdout=True,
                stderr=True,
                detach=True,
            )
            self.configure_connection()
        except docker.errors.APIError as e:
            self.logging.error("Starting Minio storage failed! Reason: {}".format(e))
            raise RuntimeError("Starting Minio storage unsuccesful")
        except Exception as e:
            self.logging.error("Starting Minio storage failed! Unknown error: {}".format(e))
            raise RuntimeError("Starting Minio storage unsuccesful")

    def configure_connection(self):
        # who knows why? otherwise attributes are not loaded
        if self._url == "":
            self._storage_container.reload()
            networks = self._storage_container.attrs["NetworkSettings"]["Networks"]
            self._url = "{IPAddress}:{Port}".format(
                IPAddress=networks["bridge"]["IPAddress"], Port=self._port
            )
            if not self._url:
                self.logging.error(
                    f"Couldn't read the IP address of container from attributes "
                    f"{json.dumps(self._instance.attrs, indent=2)}"
                )
                raise RuntimeError(
                    f"Incorrect detection of IP address for container with id {self._instance_id}"
                )
            self.logging.info("Starting minio instance at {}".format(self._url))
        self.connection = self.get_connection()

    def stop(self):
        if self._storage_container is not None:
            self.logging.info("Stopping minio container at {url}".format(url=self._url))
            self._storage_container.stop()
            self.logging.info("Stopped minio container at {url}".format(url=self._url))
        else:
            self.logging.error("Stopping minio was not succesful, storage container not known!")

    def get_connection(self):
        return minio.Minio(
            self._url,
            access_key=self._access_key,
            secret_key=self._secret_key,
            secure=False,
            http_client=Minio._define_http_client()
        )

    def _create_bucket(self, name: str, buckets: List[str] = []):
        for bucket_name in buckets:
            if name in bucket_name:
                self.logging.info(
                    "Bucket {} for {} already exists, skipping.".format(bucket_name, name)
                )
                return bucket_name
        # minio has limit of bucket name to 16 characters
        bucket_name = "{}-{}".format(name, str(uuid.uuid4())[0:16])
        try:
            self.connection.make_bucket(bucket_name, location=self.MINIO_REGION)
            self.logging.info("Created bucket {}".format(bucket_name))
            return bucket_name
        except (
            minio.error.BucketAlreadyOwnedByYou,
            minio.error.BucketAlreadyExists,
            minio.error.ResponseError,
        ) as err:
            self.logging.error("Bucket creation failed!")
            # rethrow
            raise err

    def uploader_func(self, bucket_idx, file, filepath):
        try:
            self.connection.fput_object(self.input_buckets[bucket_idx], file, filepath)
        except minio.error.ResponseError as err:
            self.logging.error("Upload failed!")
            raise (err)

    def clean(self):
        for bucket in self.output_buckets:
            objects = self.connection.list_objects_v2(bucket)
            objects = [obj.object_name for obj in objects]
            for err in self.connection.remove_objects(bucket, objects):
                self.logging.error("Deletion Error: {}".format(err))

    def download_results(self, result_dir):
        result_dir = os.path.join(result_dir, "storage_output")
        for bucket in self.output_buckets:
            objects = self.connection.list_objects_v2(bucket)
            objects = [obj.object_name for obj in objects]
            for obj in objects:
                self.connection.fget_object(bucket, obj, os.path.join(result_dir, obj))

    def clean_bucket(self, bucket: str):
        delete_object_list = map(
            lambda x: minio.DeleteObject(x.object_name),
            self.connection.list_objects(bucket_name=bucket),
        )
        errors = self.connection.remove_objects(bucket, delete_object_list)
        for error in errors:
            self.logging.error("Error when deleting object from bucket {}: {}!", bucket, error)

    def correct_name(self, name: str) -> str:
        return name

    def download(self, bucket_name: str, key: str, filepath: str):
        raise NotImplementedError()

    def exists_bucket(self, bucket_name: str) -> bool:
        return self.connection.bucket_exists(bucket_name)

    def list_bucket(self, bucket_name: str) -> List[str]:
        try:
            objects_list = self.connection.list_objects(bucket_name)
            objects: List[str]
            return [obj.object_name for obj in objects_list]
        except minio.error.NoSuchBucket:
            raise RuntimeError(f"Attempting to access a non-existing bucket {bucket_name}!")

    def list_buckets(self, bucket_name: str) -> List[str]:
        buckets = self.connection.list_buckets()
        return [bucket.name for bucket in buckets if bucket_name in bucket.name]

    def upload(self, bucket_name: str, filepath: str, key: str):
        raise NotImplementedError()

    def serialize(self) -> dict:
        if self._storage_container is not None:
            return {
                "instance_id": self._storage_container.id,
                "address": self._url,
                "port": self._port,
                "secret_key": self._secret_key,
                "access_key": self._access_key,
                "input": self.input_buckets,
                "output": self.output_buckets,
                "type": StorageTypes.MINIO,
            }
        else:
            return {}

    @staticmethod
    def deserialize(cached_config: dict, cache_client: Cache) -> "Minio":
        try:
            docker_client = docker.from_env()
            obj = Minio(docker_client, cache_client, False)
            if "instance_id" in cached_config:
                instance_id = cached_config["instance_id"]
                obj._storage_container = docker_client.containers.get(instance_id)
            else:
                obj._storage_container = None
            obj._url = cached_config["address"]
            obj._port = cached_config["port"]
            obj._access_key = cached_config["access_key"]
            obj._secret_key = cached_config["secret_key"]
            obj.input_buckets = cached_config["input"]
            obj.output_buckets = cached_config["output"]
            obj.configure_connection()
            return obj
        except docker.errors.NotFound:
            raise RuntimeError(f"Cached container {instance_id} not available anymore!")
