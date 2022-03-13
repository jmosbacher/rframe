"""Main module."""

from datetime import datetime
from typing import Any, List, Tuple, Type, Union

import pandas as pd
from pydantic.typing import NoneType

from .interfaces import get_interface
from .schema import BaseSchema, InsertionError
from .utils import camel_to_snake

IndexLabel = Union[int, float, datetime, str, slice, NoneType, List]


class RemoteFrame:
    """Implement basic indexing features similar to a pandas dataframe
    but operates on an arbitrary storage backend
    """

    schema: Type[BaseSchema]
    db: Any

    def __init__(self, schema: Type[BaseSchema], db: Any) -> None:
        self.schema = schema
        self.db = db

    @classmethod
    def from_mongodb(cls, schema, url, db, collection, **kwargs):
        import pymongo

        db = pymongo.MongoClient(url, **kwargs)[db]
        collection = db[collection]
        return cls(schema, collection)

    @property
    def name(self):
        return camel_to_snake(self.schema.__name__)

    @property
    def interface(self):
        return get_interface(self.db)

    @property
    def columns(self):
        return list(self.schema.get_column_fields())

    @property
    def index(self):
        return self.schema.get_index()

    @property
    def loc(self):
        return LocIndexer(self)

    @property
    def at(self):
        return AtIndexer(self)

    def sel_records(self, *args: IndexLabel, **kwargs: IndexLabel) -> List[dict]:
        """Queries the DB and returns the results as a list of dicts"""
        index_fields = self.index.names
        labels = {name: lbl for name, lbl in zip(index_fields, args)}
        labels.update(kwargs)
        docs = self.schema.find(self.db, **labels)
        docs = [doc.pandas_dict() for doc in docs]
        return docs

    def sel_record(self, *args: IndexLabel, **kwargs: IndexLabel) -> dict:
        """Return a single dict"""
        records = self.sel_records(*args, **kwargs)
        if records:
            return records[0]
        raise KeyError("Selection returned no records.")

    def head(self, n=10) -> pd.DataFrame:
        """Return first n documents as a pandas dataframe"""
        query = self.schema.compile_query(self.db)
        docs = query.execute(limit=n)
        index_fields = list(self.schema.get_index_fields())
        all_fields = index_fields + self.columns
        df = pd.DataFrame(docs, columns=all_fields)
        idx = [c for c in index_fields if c in df.columns]
        return df.sort_values(idx).set_index(idx)

    def unique(self, columns: Union[str, List[str]]):
        """Return unique values for each column"""
        return self.schema.unique(self.db, fields=columns)

    def min(self, columns: Union[str, List[str]]) -> Any:
        """Return the minimum value for column"""
        return self.schema.min(self.db, fields=columns )

    def max(self, columns: Union[str, List[str]]) -> Any:
        """Return the maximum value for column"""
        return self.schema.max(self.db, fields=columns)

    def sel(self, *args: IndexLabel, **kwargs: IndexLabel) -> pd.DataFrame:
        """select a subset of the data
        returns a pandas dataframe
        """
        docs = self.sel_records(*args, **kwargs)
        df = pd.json_normalize(docs)

        # df = pd.DataFrame(docs, columns=self.index.names + self.columns)

        idx = [c for c in self.schema.get_index_fields() if c in df.columns]
        return df.sort_values(idx).set_index(idx)

    def set(self, *args: IndexLabel, **kwargs: IndexLabel) -> BaseSchema:
        """Insert data by index"""
        labels = {name: lbl for name, lbl in zip(self.index.names, args)}
        kwargs.update(labels)
        doc = self.schema(**kwargs)
        return doc.save(self.db)

    def concat(
        self, records: Union[pd.DataFrame, List[dict]]
    ) -> Tuple[List[dict], List[dict], List[dict]]:
        """Insert multiple records into the DB"""
        if isinstance(records, pd.DataFrame):
            records = self.schema.from_pandas(records)

        succeeded = []
        failed = []
        errors = []
        for record in records:
            if not isinstance(doc, self, self.schema):
                doc = self.schema(**record)
            try:
                doc.save(self.db)
                succeeded.append(doc)
            except InsertionError as e:
                failed.append(doc)
                errors.append(str(e))

        return succeeded, failed, errors

    def __getitem__(self, index: Tuple[IndexLabel, ...]) -> "RemoteSeries":
        if isinstance(index, str) and index in self.columns:
            return RemoteSeries(self, index)
        if isinstance(index, tuple) and index[0] in self.columns:
            return RemoteSeries(self, index[0])[index[1:]]
        raise KeyError(f"{index} is not a dataframe column.")

    def __call__(self, column: str, **index: IndexLabel) -> pd.DataFrame:
        index = tuple(index.get(k, None) for k in self.index.names)
        return self.at[index, column]

    def __dir__(self) -> List[str]:
        return self.columns + super().__dir__()

    def __getattr__(self, name: str) -> "RemoteSeries":
        if name != "columns" and name in self.columns:
            return self[name]
        raise AttributeError(name)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"index={self.index.names},"
            f"columns={self.columns})"
        )


class RemoteSeries:
    obj: RemoteFrame
    column: str

    def __init__(self, obj: RemoteFrame, column: str) -> None:
        self.obj = obj
        self.column = column

    def __getitem__(
        self, index: Union[IndexLabel, Tuple[IndexLabel, ...]]
    ) -> pd.DataFrame:
        if not isinstance(index, tuple):
            index = (index,)
        return self.obj.sel(*index)[self.column]

    def sel(self, *args: IndexLabel, **kwargs: IndexLabel) -> pd.DataFrame:
        df = self.obj.sel(*args, **kwargs)
        return df[self.column]

    def sel_values(self, *args: IndexLabel, **kwargs: IndexLabel) -> List[Any]:
        docs = self.obj.sel_records(*args, **kwargs)
        return [doc[self.column] for doc in docs]

    def sel_value(self, *args: IndexLabel, **kwargs: IndexLabel) -> Any:
        values = self.sel_values(*args, **kwargs)
        if values:
            return values[0]
        raise KeyError("Selection returned no values.")

    def set(self, *args: IndexLabel, **kwargs: IndexLabel):
        raise InsertionError(
            "Cannot set values on a RemoteSeries object," "use the RemoteDataFrame."
        )

    def unique(self):
        return self.obj.unique(self.column)

    def min(self):
        return self.obj.min(self.column)

    def max(self):
        return self.obj.max(self.column)

    def __repr__(self) -> str:
        return f"RemoteSeries(index={self.obj.index.names}," f"column={self.column})"


class Indexer:
    def __init__(self, obj: RemoteFrame):
        self.obj = obj


class LocIndexer(Indexer):
    def __call__(self, *args: IndexLabel, **kwargs: IndexLabel) -> pd.DataFrame:
        return self.obj.sel(*args, **kwargs)

    def __getitem__(self, index: Tuple[IndexLabel]) -> pd.DataFrame:
        columns = None

        if isinstance(index, tuple) and len(index) == 2:
            index, columns = index
            if not isinstance(columns, list):
                columns = [columns]
            if not all([c in self.obj.columns for c in columns]):
                if not isinstance(index, tuple):
                    index = (index,)
                index = index + tuple(columns)
                columns = None

        elif isinstance(index, tuple) and len(index) == len(self.obj.columns) + 1:
            index, columns = index[:-1], index[-1]

        if not isinstance(index, tuple):
            index = (index,)

        df = self.obj.sel(*index)

        if columns is not None:
            df = df[columns]

        return df

    def __setitem__(self, key: Any, value: Union[dict, BaseSchema]) -> BaseSchema:
        if not isinstance(key, tuple):
            key = (key,)

        if isinstance(value, self.obj.schema):
            value = value.dict()

        if not isinstance(value, dict):
            value = {"value": value}

        return self.obj.set(*key, **value)


class AtIndexer(Indexer):
    def __getitem__(self, key: Tuple[Tuple[IndexLabel, ...], str]) -> Any:

        if not (isinstance(key, tuple) and len(key) == 2):
            raise KeyError(
                "ill-defined location. Specify "
                ".at[index,column] where index can be a tuple."
            )

        index, column = key

        if column not in self.obj.columns:
            raise KeyError(f"{column} not found. Valid columns are: {self.obj.columns}")

        if not isinstance(index, tuple):
            index = (index,)

        if any([isinstance(idx, (slice, list, type(None))) for idx in index]):
            raise KeyError(f"{index} is not unique index.")

        if len(index) < len(self.obj.index.names):
            KeyError(f"{index} is an under defined index.")

        return self.obj[column].sel_value(*index)
