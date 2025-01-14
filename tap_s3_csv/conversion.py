"""
Module to guess csv columns' types and build Json schema.
"""
import csv
import io

from typing import Dict, List
from messytables import CSVTableSet, headers_guess, headers_processor, offset_processor, type_guess
from messytables.types import DecimalType, IntegerType


def generate_schema(samples: List[Dict], table_spec: Dict) -> Dict:
    """
    Guess columns types from the given samples and build json schema
    :param samples: List of dictionaries containing samples data from csv file(s)
    :param table_spec: table/stream specs given in the tap definition
    :return: dictionary where the keys are the headers and values are the guessed types - compatible with json schema
    """
    schema = {}

    table_set = CSVTableSet(_csv2bytesio(samples))

    row_set = table_set.tables[0]

    offset, headers = headers_guess(row_set.sample)
    row_set.register_processor(headers_processor(headers))
    row_set.register_processor(offset_processor(offset + 1))

    types = type_guess(row_set.sample, strict=True)

    for header, header_type in zip(headers, types):

        date_overrides = set(table_spec.get('date_overrides', []))

        if header in date_overrides:
            schema[header] = {'type': ['null', 'string'], 'format': 'date-time'}
        else:
            if isinstance(header_type, IntegerType):
                schema[header] = {
                    'type': ['null', 'integer']
                }
            elif isinstance(header_type, DecimalType):
                schema[header] = {
                    'type': ['null', 'number']
                }
            else:
                schema[header] = {
                    'type': ['null', 'string']
                }

    return schema


def generate_string_schema(samples: List[Dict], table_spec: Dict) -> Dict:
    """
    Build json schema, all columns in the headers would be string.
    with format date-time if date_overrides has been configured for the table.
    :param samples: List of dictionaries containing samples data from csv file(s)
    :param table_spec: table/stream specs given in the tap definition
    :return: json schema dictionary representing  the table
    """
    schema = {}
    date_overrides = set(table_spec.get('date_overrides', []))

    for sample in samples:
        for header in sample.keys():
            schema[header] = {'type': ['null', 'string']}

            if header in date_overrides:
                schema[header]['format'] = 'date-time'

    return schema


def _csv2bytesio(data: List[Dict]) -> io.BytesIO:
    """
    Converts a list of dictionaries to a csv BytesIO which is a csv file like object
    :param data: List of dictionaries to turn into csv like structure
    :return: BytesIO, a file like object in memory
    """
    with io.StringIO() as sio:

        header = set()

        for datum in data:
            header.update(list(datum.keys()))

        writer = csv.DictWriter(sio, fieldnames=header)

        writer.writeheader()
        writer.writerows(data)

        return io.BytesIO(sio.getvalue().strip('\r\n').encode('utf-8'))
