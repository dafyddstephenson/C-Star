from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import charset_normalizer
import requests

from cstar.base.utils import _run_cmd
from cstar.io import (
    LocalBinaryFileStager,
    LocalTextFileStager,
    RemoteBinaryFileStager,
    RemoteRepositoryStager,
    RemoteTextFileStager,
    StagedData,
    StagedDataCollection,
    Stager,
)


class SourceType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    REPOSITORY = "repository"


class LocationType(Enum):
    HTTP = "http"
    PATH = "path"


class FileEncoding(Enum):
    TEXT = "text"
    BINARY = "binary"


class _SourceInspector:
    def __init__(self, location: str):
        self.location = location

    @property
    def location_type(self) -> LocationType:
        """Get the location type (e.g. "path" or "url") from the "location"
        attribute.
        """
        urlparsed_location = urlparse(self.location)
        if all([urlparsed_location.scheme, urlparsed_location.netloc]):
            return LocationType.HTTP
        elif self._location_as_path.exists():
            return LocationType.PATH
        else:
            raise ValueError(
                f"{self.location} is not a recognised URL or local path pointing to an existing file or directory"
            )

    @property
    def _location_as_path(self) -> Path:
        """Return self.location as a Path for parsing"""
        if self.location_type is LocationType.HTTP:
            return Path(urlparse(self.location).path)
        elif self.location_type is LocationType.PATH:
            return Path(self.location).resolve()
        raise ValueError(f"Cannot convert location {self.location} to Path")

    @property
    def _is_repository(self) -> bool:
        """Checks if self.location describes a repository using a git ls-remote subprocess."""
        try:
            _run_cmd(f"git ls-remote {self.location}", raise_on_error=True)
            return True
        except RuntimeError:
            return False

    @property
    def source_type(self) -> SourceType:
        """Infer source type (file/directory/repository) from 'location'."""
        if self._is_repository:
            return SourceType.REPOSITORY
        elif self.location_type is LocationType.HTTP:
            if (not self._http_is_html) and (self.suffix):
                return SourceType.FILE
        elif self.location_type is LocationType.PATH:
            resolved_path = Path(self.location).resolve()
            if resolved_path.is_file():
                return SourceType.FILE
            elif resolved_path.is_dir():
                return SourceType.DIRECTORY
        raise ValueError(
            f"{self.location} does not appear to point to a valid source type. "
            "Valid source types: \n"
            "\n".join([value.value for value in SourceType])
        )

    @property
    def _http_is_html(self) -> bool:
        """Determine if the location is a HTML page.

        As certain services provide URLs that resemble direct filepaths, but
        redirect to, e.g., login pages, this property queries whether the location
        is or is not HTML.
        """
        r = requests.head(self.location, allow_redirects=True, timeout=10)
        content_type = r.headers.get("Content-Type", "").lower()
        return content_type.startswith("text/html")

    @property
    def suffix(self) -> str:
        """Get the extension from `location`"""
        return self._location_as_path.suffix

    @property
    def file_encoding(self) -> FileEncoding | None:
        """Look up file encoding based on source_type."""
        if self.source_type is not SourceType.FILE:
            return None

        def get_header_bytes(location, n_bytes=512) -> bytes:
            if self.location_type is LocationType.HTTP:
                response = requests.get(location, stream=True, allow_redirects=True)
                response.raw.decode_content = True
                header_bytes = response.raw.read(n_bytes)
            elif self.location_type is LocationType.PATH:
                with open(location, "rb") as f:
                    header_bytes = f.read(n_bytes)
            return header_bytes

        best_encoding = charset_normalizer.from_bytes(
            get_header_bytes(self.location)
        ).best()
        if best_encoding:
            return FileEncoding.TEXT
        else:
            return FileEncoding.BINARY

    def infer_class(self) -> type["SourceData"]:
        """Logic to determine the correct SourceData subclass based on the above
        characteristics.
        """
        # Remote sources
        if self.location_type is LocationType.HTTP:
            if self.source_type is SourceType.REPOSITORY:
                return RemoteRepositorySource

            if self.source_type is SourceType.FILE:
                if self.file_encoding is FileEncoding.BINARY:
                    return RemoteBinaryFileSource
                elif self.file_encoding is FileEncoding.TEXT:
                    return RemoteTextFileSource

        # Local sources
        if (self.location_type is LocationType.PATH) and (
            self.source_type is SourceType.FILE
        ):
            if self.file_encoding is FileEncoding.TEXT:
                return LocalTextFileSource
            elif self.file_encoding is FileEncoding.BINARY:
                return LocalBinaryFileSource

        raise ValueError(
            f"Unable to determine an appropriate stager for data at {self.location}"
        )


class SourceData(ABC):
    def __init__(self, location: str | Path, identifier: str | None = None):
        self._location = str(location)
        self._identifier = identifier

    @classmethod
    def from_location(cls, location: str | Path, identifier: str | None = None):
        cls = _SourceInspector(location=str(location)).infer_class()

        return cls(location=location, identifier=identifier)

    @property
    def location(self) -> str:
        return self._location

    @property
    def identifier(self) -> str | None:
        return self._identifier

    @property
    @abstractmethod
    def _location_as_path(self) -> Path:
        """Return self.location as a Path for parsing"""
        pass

    @property
    def filename(self) -> str:
        """Get the filename from `location`"""
        return self._location_as_path.name

    @property
    def suffix(self) -> str:
        """Get the extension from `location`"""
        return self._location_as_path.suffix

    @property
    @abstractmethod
    def stager(self) -> "Stager":
        pass

    def stage(self, target_dir: str | Path) -> "StagedData":
        return self.stager.stage(target_dir=Path(target_dir), source=self)


class RemoteSourceData(SourceData, ABC):
    @property
    def _location_as_path(self) -> Path:
        return Path(urlparse(self.location).path)


class RemoteBinaryFileSource(RemoteSourceData):
    @property
    def file_hash(self) -> str | None:
        return self._identifier

    @property
    def stager(self):
        return RemoteBinaryFileStager


class RemoteTextFileSource(RemoteSourceData):
    @property
    def file_hash(self) -> str | None:
        return self._identifier

    @property
    def stager(self):
        return RemoteTextFileStager


class RemoteRepositorySource(RemoteSourceData):
    @property
    def checkout_target(self) -> str | None:
        return self._identifier

    @property
    def stager(self):
        return RemoteRepositoryStager


class LocalSourceData(SourceData, ABC):
    @property
    def _location_as_path(self) -> Path:
        return Path(self.location).resolve()


class LocalBinaryFileSource(SourceData):
    @property
    def stager(self):
        return LocalBinaryFileStager


class LocalTextFileSource(SourceData):
    @property
    def stager(self):
        return LocalTextFileStager


class SourceDataCollection:
    def __init__(self, sources: Iterable[SourceData] = ()):
        self._sources: list[SourceData] = list(sources)
        self._validate()

    def _validate(self):
        for s in self._sources:
            if s.source_type in [SourceType.DIRECTORY, SourceType.REPOSITORY]:
                raise TypeError(
                    f"Cannot create SourceDataCollection with data of source type '{s.source_type.value}'"
                )

    def __len__(self) -> int:
        return len(self._sources)

    def __getitem__(self, idx: int) -> SourceData:
        return self._sources[idx]

    def __iter__(self) -> Iterator[SourceData]:
        return iter(self._sources)

    def append(self, source: SourceData) -> None:
        self._sources.append(source)
        self._validate()

    @property
    def locations(self) -> list[str]:
        return [s.location for s in self._sources]

    @classmethod
    def from_locations(
        cls, locations: Sequence[str | Path], identifiers: Sequence[str | None]
    ) -> "SourceDataCollection":
        """Create a SourceDataCollection from a list of locations with optional
        parallel hash and checkout_target lists.
        """
        n = len(locations)
        identifiers = identifiers or [None] * n

        if not (len(identifiers) == n):
            raise ValueError("Length mismatch between inputs")

        sources = [
            SourceData.from_location(location=loc, identifier=id)
            for loc, id in zip(locations, identifiers)
        ]
        return cls(sources)

    @property
    def sources(self) -> list[SourceData]:
        return self._sources

    def stage(self, target_dir: str | Path) -> StagedDataCollection:
        staged_data_instances = []
        for s in self.sources:
            staged_data = s.stage(target_dir=target_dir)
            staged_data_instances.append(staged_data)
        return StagedDataCollection(items=staged_data_instances)
