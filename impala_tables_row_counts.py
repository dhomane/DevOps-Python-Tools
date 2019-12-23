#!/usr/bin/env python
#  vim:ts=4:sts=4:sw=4:et
#
#  Author: Hari Sekhon
#  Date: 2019-11-26 10:08:52 +0000 (Tue, 26 Nov 2019)
#
#  https://github.com/harisekhon/devops-python-tools
#
#  License: see accompanying Hari Sekhon LICENSE file
#
#  If you're using my code you're welcome to connect with me on LinkedIn
#  and optionally send me feedback to help steer this or other code I publish
#
#  https://www.linkedin.com/in/harisekhon
#

"""

Connect to an Impala node and get rows counts for all tables in all databases,
or only those matching given db / table / partition value regexes

Tested on CDH 5.10, Impala 2.7.0 with Kerberos

Due to a thrift / impyla bug this needs exactly thrift==0.9.3, see

https://github.com/cloudera/impyla/issues/286

If you get an error like this:

ERROR:impala.hiveserver2:Failed to open transport (tries_left=1)
...
TTransportException: TSocket read 0 bytes

then check your --kerberos and --ssl settings match the cluster's settings
(Thrift and Kerberos have the worst error messages ever)

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import logging
import os
import re
import socket
import sys
import impala
from impala.dbapi import connect

__author__ = 'Hari Sekhon'
__version__ = '0.1.0'

logging.basicConfig()
log = logging.getLogger(os.path.basename(sys.argv[0]))

host_envs = [
    'IMPALA_HOST',
    'HOST'
]

port_envs = [
    'IMPALA_PORT',
    'PORT'
]

def getenvs(keys, default=None):
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default

def parse_args():
    parser = argparse.ArgumentParser(
        description="Gets row counts for all impala tables/partitions matching database / table / partition regexes")
    parser.add_argument('-H', '--host', default=getenvs(host_envs, socket.getfqdn()),\
                        help='Impala node ' + \
                             '(default: fqdn of local host, $' + ', $'.join(host_envs) + ')')
    parser.add_argument('-P', '--port', type=int, default=getenvs(port_envs, 21050),
                        help='Impala port (default: 21050, ' + ', $'.join(port_envs) + ')')
    parser.add_argument('-d', '--database', default='.*', help='Database regex (default: .*)')
    parser.add_argument('-t', '--table', default='.*', help='Table regex (default: .*)')
    parser.add_argument('-p', '--partition', default='.*', help='Partition regex (default: .*)')
    parser.add_argument('-k', '--kerberos', action='store_true', help='Use Kerberos (you must kinit first)')
    parser.add_argument('-n', '--krb5-service-name', default='impala',
                        help='Service principal (default: \'impala\')')
    parser.add_argument('-S', '--ssl', action='store_true', help='Use SSL')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose mode')
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.INFO)
    if args.verbose > 1 or os.getenv('DEBUG'):
        log.setLevel(logging.DEBUG)

    return args

def connect_db(args, database):
    auth_mechanism = None
    if args.kerberos:
        auth_mechanism = 'GSSAPI'

    log.info('connecting to %s:%s database %s', args.host, args.port, database)
    return connect(
        host=args.host,
        port=args.port,
        auth_mechanism=auth_mechanism,
        use_ssl=args.ssl,
        #user=user,
        #password=password,
        database=database,
        kerberos_service_name=args.krb5_service_name
        )

def main():
    args = parse_args()

    try:
        database_regex = re.compile(args.database, re.I)
        table_regex = re.compile(args.table, re.I)
        partition_regex = re.compile(args.partition, re.I)
    except re.error as _:
        log.error('error in provided regex: %s', _)
        sys.exit(3)

    conn = connect_db(args, 'default')

    log.info('querying databases')
    with conn.cursor() as db_cursor:
        db_cursor.execute('show databases')
        for db_row in db_cursor:
            database = db_row[0]
            if not database_regex.search(database):
                log.debug("skipping database '%s', does not match regex '%s'", database, args.database)
                continue
            log.info('querying tables for database %s', database)
            #db_conn = connect_db(args, database)
            #with db_conn.cursor() as table_cursor:
            with conn.cursor() as table_cursor:
                # doesn't support parameterized query quoting from dbapi spec
                #table_cursor.execute('use %(database)s', {'database': database})
                table_cursor.execute('use {}'.format(database))
                table_cursor.execute('show tables')
                for table_row in table_cursor:
                    table = table_row[0]
                    if not table_regex.search(table):
                        log.debug("skipping database '%s' table '%s', does not match regex '%s'", \
                                  database, table, args.table)
                        continue
                    try:
                        get_row_counts(conn, args, database, table, partition_regex)
                    except impala.error.OperationalError as _:
                        log.error(_)

def get_row_counts(conn, args, database, table, partition_regex):
    log.info("getting partitions for database '%s' table '%s'", database, table)
    with conn.cursor() as partition_cursor:
        # doesn't support parameterized query quoting from dbapi spec
        partition_cursor.execute('use {db}'.format(db=database))
        try:
            partition_cursor.execute('show partitions {table}'.format(table=table))
            for partitions_row in partition_cursor:
                partition_key = partitions_row[0]
                partition_value = partitions_row[1]
                if not partition_regex.match(partition_value):
                    log.debug("skipping database '%s' table '%s' partition key '%s' value '%s', " +
                              "value does not match regex '%s'",
                              database,
                              table,
                              partition_key,
                              partition_value,
                              args.partition)
                    continue
                # doesn't support parameterized query quoting from dbapi spec
                partition_cursor.execute('SELECT COUNT(*) FROM {db}.{table} WHERE {key}={value}'\
                                      .format(db=database, table=table, key=partition_key, value=partition_value))
                for result in partition_cursor:
                    row_count = result[0]
                    print('{db}.{table}.{key}={value}\t{row_count}'.format(\
                            db=database, table=table, key=partition_key, value=partition_value, row_count=row_count))
        except (impala.error.OperationalError, impala.error.HiveServer2Error) as _:
            if 'Table is not partitioned' not in str(_):
                raise
            log.info("no partitions found for database '%s' table '%s', getting row counts for whole table",
                     database, table)
            with conn.cursor() as table_cursor:
                log.info("running SELECT COUNT(*) FROM %s.%s", database, table)
                # doesn't support parameterized query quoting from dbapi spec
                table_cursor.execute('SELECT COUNT(*) FROM {db}.{table}'.format(db=database, table=table))
                for result in table_cursor:
                    row_count = result[0]
                    print('{db}.{table}\t{row_count}'.format(db=database, table=table, row_count=row_count))


if __name__ == '__main__':
    main()
