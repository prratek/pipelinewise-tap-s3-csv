"""
Syncing related functions
"""

import codecs
import sys
import csv
from typing import Dict

from singer import metadata, Transformer, utils, get_bookmark, write_bookmark, write_state, write_record, get_logger
from singer.transform import SchemaMismatch

from tap_s3_csv import s3

LOGGER = get_logger('tap_s3_csv')

SDC_EXTRA_COLUMN = "_sdc_extra"


def get_row_iterator(iterable, options=None, headers_in_catalog=None, with_duplicate_headers=False, header_fields=None):
    """
    Accepts an iterable, options and returns a csv.DictReader which can be used to yield CSV rows.
    """

    options = options or {}
    reader = []
    headers = set()
    file_stream = codecs.iterdecode(iterable, encoding='utf-8')
    delimiter = options.get('delimiter', ',')
    quotechar = options.get('quotechar', '"')
    escapechar = options.get('escapechar')

    # Inject headers from configs.
    field_names = None
    if headers_in_catalog:
        field_names = header_fields

    reader = csv.DictReader(
        (line.replace('\0', '') for line in file_stream),
        fieldnames=field_names,
        restkey=SDC_EXTRA_COLUMN,
        delimiter=delimiter,
        escapechar=escapechar,
        quotechar=quotechar
    )
    try:
        headers = set(reader.fieldnames)

    except TypeError:
        # handle NoneType error when empty file is found: tap-SFTP
        pass

    if options.get('key_properties'):
        key_properties = set(options['key_properties'])
        if not key_properties.issubset(headers):
            raise Exception('CSV file missing required headers: {}'
                            .format(key_properties - headers))

    if options.get('date_overrides'):
        date_overrides = set(options['date_overrides'])
        if not date_overrides.issubset(headers):
            raise Exception('CSV file missing date_overrides headers: {}'
                            .format(date_overrides - headers))

    return reader


def _remove_escaped_quotes(rec: Dict) -> Dict:
    res = rec
    if res.get("url"):
        res["url"] = res["url"].replace('\"', "")
    return res


def sync_stream(config: Dict, state: Dict, table_spec: Dict, stream: Dict) -> int:
    """
    Sync the stream
    :param config: Connection and stream config
    :param state: current state
    :param table_spec: table specs
    :param stream: stream
    :return: count of streamed records
    """
    table_name = table_spec['table_name']
    modified_since = utils.strptime_with_tz(get_bookmark(state, table_name, 'modified_since') or
                                            config['start_date'])

    LOGGER.info('Syncing table "%s".', table_name)
    LOGGER.info('Getting files modified since %s.', modified_since)

    s3_files = s3.get_input_files_for_table(
        config, table_spec, modified_since)

    records_streamed = 0

    # We sort here so that tracking the modified_since bookmark makes
    # sense. This means that we can't sync s3 buckets that are larger than
    # we can sort in memory which is suboptimal. If we could bookmark
    # based on anything else then we could just sync files as we see them.
    for s3_file in sorted(s3_files, key=lambda item: item['last_modified']):
        records_streamed += sync_table_file(
            config, s3_file['key'], table_spec, stream)

        state = write_bookmark(state, table_name, 'modified_since', s3_file['last_modified'].isoformat())
        write_state(state)

    LOGGER.info('Wrote %s records for table "%s".', records_streamed, table_name)

    return records_streamed

def sync_table_file(config: Dict, s3_path: str, table_spec: Dict, stream: Dict) -> int:
    """
    Sync a given csv found file
    :param config: tap configuration
    :param s3_path: file path given by S3
    :param table_spec: tables specs
    :param stream: Stream data
    :return: number of streamed records
    """
    LOGGER.info('Syncing file "%s".', s3_path)

    bucket = config['bucket']
    table_name = table_spec['table_name']

    s3_file_stream = s3.get_file_stream(config, s3_path)
    # We observed data who's field size exceeded the default maximum of
    # 131072. We believe the primary consequence of the following setting
    # is that a malformed, wide CSV would potentially parse into a single
    # large field rather than giving this error, but we also think the
    # chances of that are very small and at any rate the source data would
    # need to be fixed. The other consequence of this could be larger
    # memory consumption but that's acceptable as well.
    csv.field_size_limit(sys.maxsize)

    # csv.get_row_iterator will check key-properties exist in the csv
    # so we need to give them the list minus the meta field such as SDC_SOURCE_FILE_COLUMN or others
    reduced_table_spec = {"key_properties": table_spec.get("key_properties", []).copy()}
    if s3.SDC_SOURCE_BUCKET_COLUMN in reduced_table_spec["key_properties"]:
        reduced_table_spec["key_properties"].remove(s3.SDC_SOURCE_BUCKET_COLUMN)
    if s3.SDC_SOURCE_FILE_COLUMN in reduced_table_spec["key_properties"]:
        reduced_table_spec["key_properties"].remove(s3.SDC_SOURCE_FILE_COLUMN)
    if s3.SDC_SOURCE_LINENO_COLUMN in reduced_table_spec["key_properties"]:
        reduced_table_spec["key_properties"].remove(s3.SDC_SOURCE_LINENO_COLUMN)

    custom_headers = False
    fields = None


    # fetches schema from catalog.
    if config.get('infer_schema', None) and config['infer_schema'].lower() == "false":
        custom_headers = True
        fields = list(stream['schema']['properties'].keys())
        LOGGER.info('Custom headers is set to True, proceeding with the following fields: "%d"', fields)


    iterator = get_row_iterator(
        iterable=s3_file_stream,
        options=reduced_table_spec,
        headers_in_catalog=custom_headers,
        header_fields=fields
        )  # pylint:disable=protected-access

    records_synced = 0
    mismatches = 0

    for row in iterator:
        custom_columns = {
            s3.SDC_SOURCE_BUCKET_COLUMN: bucket,
            s3.SDC_SOURCE_FILE_COLUMN: s3_path,

            # index zero, +1 for header row
            s3.SDC_SOURCE_LINENO_COLUMN: records_synced + 2
        }
        rec = {**row, **custom_columns}

        try:
            with Transformer() as transformer:
                to_write = transformer.transform(rec, stream['schema'], metadata.to_map(stream['metadata']))

            write_record(table_name, to_write)
            records_synced += 1

        except SchemaMismatch:
            mismatches += 1

    if mismatches > 0:
        LOGGER.warning(f"Encountered {mismatches} SchemaMismatch errors in {s3_path}")

    return records_synced
