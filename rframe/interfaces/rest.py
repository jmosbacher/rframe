from typing import Union

from .base import BaseDataQuery, DatasourceInterface
from ..indexes import Index, InterpolatingIndex, IntervalIndex, MultiIndex
from ..utils import singledispatchmethod
from ..rest_client import BaseRestClient, RestClient


class RestQuery(BaseDataQuery):
    client: BaseRestClient
    params: dict

    def __init__(self, client: BaseRestClient, params=None):
        self.client = client
        self.params = params if params is not None else {}

    def execute(self, limit: int = None, skip: int = None):
        return self.client.query(**self.params, limit=limit, skip=skip)

def serializable_interval(interval):
    if isinstance(interval, list):
        return [serializable_interval(iv) for iv in interval]

    if isinstance(interval, tuple):
        left, right = interval
    elif isinstance(interval, dict):
        left, right = interval["left"], interval["right"]
    elif isinstance(interval, slice):
        left, right = interval.start, interval.stop
    elif hasattr(interval, "left") and hasattr(interval, "right"):
        left, right = interval.left, interval.right
    else:
        left = right = interval
    
    interval = {'left': left, 'right': right}

    return interval


@DatasourceInterface.register_interface(BaseRestClient)
class RestInterface(DatasourceInterface):

    @classmethod
    def from_url(cls, url: str, headers=None, **kwargs):
        if url.startswith("http://") or url.startswith("https://"):
            client = RestClient(url, headers)
            return cls(client)

        raise NotImplementedError

    @singledispatchmethod
    def compile_query(self, index, label):
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support {type(index)} indexes."
        )

    @compile_query.register(InterpolatingIndex)
    @compile_query.register(Index)
    def simple_query(self, index: Union[Index,InterpolatingIndex], label):
        return RestQuery(self.source, {index.name: label})

    @compile_query.register(IntervalIndex)
    def interval_query(self, index: IntervalIndex, interval):
        interval = serializable_interval(interval)
        return RestQuery(self.source, {index.name: interval})

    @compile_query.register(list)
    @compile_query.register(tuple)
    @compile_query.register(MultiIndex)
    def multi_query(self, indexes, labels):
        if isinstance(indexes, MultiIndex):
            indexes = indexes.indexes
            labels = labels.values()
        
        params = {}
        for idx, label in zip(indexes, labels):
            query = self.compile_query(idx, label)
            if idx.name in query.params:
                params[idx.name] = query.params[idx.name]

        return RestQuery(self.source, params)

    def insert(self, doc):
        return self.source.insert(doc)