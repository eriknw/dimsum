import inspect
from typing import Set, List, Tuple, Optional
from .schema import Schema
import grblas
import numpy as np
import pandas as pd


class Flat:
    """
    Coded data in a flat structure, represented by a GraphBLAS Vector
    """
    def __init__(self, vector, schema: Schema, dims: Set[str]):
        self.vector = vector
        self.schema = schema
        self.dims = set(dims)

    def __repr__(self):
        df = self.to_dataframe()
        return repr(df)

    def _repr_html_(self):
        df = self.to_dataframe()
        return df._repr_html_()

    def __len__(self):
        return self.vector.nvals

    @property
    def dims_list(self):
        """
        Returns the dimensions as a list, ordered according to the schema
        """
        return [n for n in self.schema.names if n in self.dims]

    def _normalize_dims(self, dims) -> Set[str]:
        if type(dims) is set:
            return dims
        if isinstance(dims, str):
            return {dims}
        return set(dims)

    def _compute_missing_dims(self, subset: Set[str]) -> Set[str]:
        if isinstance(subset, str):
            subset = {subset}
        extra_dims = subset - self.dims
        if extra_dims:
            raise ValueError(f"Dimensions {extra_dims} requested, but not available in object")
        return self.dims - subset

    def pivot(self, *, left: Optional[Set[str]] = None, top: Optional[Set[str]] = None) -> "Pivot":
        # Check dimensions
        if left is None and top is None:
            raise TypeError("Must provide either left or top dimensions")
        elif left is not None:
            left = self._normalize_dims(left)
            top = self._compute_missing_dims(left)
        elif top is not None:
            top = self._normalize_dims(top)
            left = self._compute_missing_dims(top)
        else:
            left = self._normalize_dims(left)
            top = self._normalize_dims(top)
            top_verify, left = self._compute_missing_dims(left)
            if top_verify != top:
                raise ValueError("Union of left and top must equal the dimensions in the object")

        if not left:
            raise ValueError("left dimensions are empty")
        if not top:
            raise ValueError("top dimensions are empty")

        # Perform pivot
        left_mask = self.schema.dims_to_mask(left)
        top_mask = self.schema.dims_to_mask(top)
        index, vals = self.vector.to_values()
        rows = index & left_mask
        cols = index & top_mask
        matrix = grblas.Matrix.from_values(rows, cols, vals, nrows=left_mask + 1, ncols=top_mask + 1)
        return Pivot(matrix, self.schema, left, top)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, schema: Schema, dims: List[str], value_column: str) -> "Flat":
        """
        Converts a DataFrame to a Flat by indicating the dimensions and value column

        :param df: pd.DataFrame
        :param schema: Schema
        :param dims: List[str] list of column headers
        :param value_column: str column header
        :return: Flat
        """
        index = schema.encode_many(df[dims])
        vals = df[value_column].values
        dim_mask = schema.dims_to_mask(dims)
        vec = grblas.Vector.from_values(index, vals, size=dim_mask + 1)
        return cls(vec, schema, dims)

    @classmethod
    def from_series(cls, s: pd.Series, schema: Schema) -> "Flat":
        """
        The Series must have a named index or MultiIndex. The name or level names will be used
        as the dimension names.

        :param s: pd.Series
        :param schema: Schema
        :return: Flat
        """
        if isinstance(s.index, pd.MultiIndex):
            dims = s.index.names
        else:
            if not s.index.name:
                err_msg = (
                    "Series index does not have a name. Unable to infer dimension.\n"
                    "When creating the series, ensure the index has a name:\n"
                    "s = pd.Series([1, 2, 3], index=pd.Index(['S', 'M', 'L'], name='size'))"
                )
                raise TypeError(err_msg)
            dims = [s.index.name]
        df = s.to_frame("* value *").reset_index()
        return cls.from_dataframe(df, schema, dims, "* value *")

    def to_series(self) -> pd.Series:
        """
        Converts the Flat into a Series with a named index or a MultiIndex

        :return: pd.Series
        """
        df = self.to_dataframe("* values *")
        dims = self.dims_list
        if len(dims) == 1:
            dims = dims[0]
        return df.set_index(dims)["* values *"]

    def to_dataframe(self, value_column="* values *") -> pd.DataFrame:
        """
        Converts the Flat into a DataFrame

        :param value_column: str name of column containing the values
        :return: pd.DataFrame
        """
        index, vals = self.vector.to_values()
        df = self.schema.decode_many(index, self.dims_list)
        df[value_column] = vals
        return df


class Pivot:
    """
    Coded data in a pivoted structure, represented by a GraphBLAS Matrix
    """
    def __init__(self, matrix, schema: Schema, left: Set[str], top: Set[str]):
        self.matrix = matrix
        self.schema = schema
        self.left = set(left)
        self.top = set(top)

    def __repr__(self):
        with pd.option_context('display.multi_sparse', False):
            df = self.to_dataframe()
            return repr(df)

    def _repr_html_(self):
        with pd.option_context('display.multi_sparse', False):
            df = self.to_dataframe()
            return df._repr_html_()

    def flatten(self) -> Flat:
        rows, cols, vals = self.matrix.to_values()
        index = rows | cols
        vector = grblas.Vector.from_values(index, vals)
        combo_dims = self.left | self.top
        return Flat(vector, self.schema, combo_dims)

    op_default = inspect.signature(grblas.Matrix.reduce_rows).parameters['op'].default
    def reduce_rows(self, op=op_default):
        vector = self.matrix.reduce_rows(op).new()
        return Flat(vector, self.schema, self.left)

    op_default = inspect.signature(grblas.Matrix.reduce_columns).parameters['op'].default
    def reduce_columns(self, op=op_default):
        vector = self.matrix.reduce_columns(op).new()
        return Flat(vector, self.schema, self.top)

    del op_default

    def to_dataframe(self):
        left_dims = [n for n in self.schema.names if n in self.left]
        top_dims = [n for n in self.schema.names if n in self.top]
        rows, cols, vals = self.matrix.to_values()
        row_unique, row_reverse = np.unique(rows, return_inverse=True)
        col_unique, col_reverse = np.unique(cols, return_inverse=True)
        row_index = self.schema.decode_many(row_unique, left_dims).set_index(left_dims).index
        col_index = self.schema.decode_many(col_unique, top_dims).set_index(top_dims).index
        df = pd.DataFrame(index=row_index, columns=col_index)
        df.values[row_reverse, col_reverse] = vals
        df = df.where(pd.notnull(df), "")
        return df
