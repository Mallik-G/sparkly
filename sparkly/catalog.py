#
# Copyright 2017 Tubular Labs, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import uuid


class SparklyCatalog(object):
    """A set of tools to interact with HiveMetastore."""

    def __init__(self, spark):
        """Constructor.

        Args:
            spark (sparkly.SparklySession)
        """
        self._spark = spark

    def create_table(self, table_name, path=None, source=None, schema=None, **options):
        """Create table in the metastore.

        Extend ``SparkSession.Catalog.createExternalTable`` by accepting
        a ``mode='overwrite'`` option which creates the table even if a
        table with the same name already exists. All other args are
        exactly the same.

        Note:
            If the table exists, create two unique names, one for the
            new and one for the old instance, then try to swap names
            and drop the "old" instance. If any step fails, the metastore
            might be currently left at a broken state.

        Args:
            mode (str): if set to ``'overwrite'``, drop any table of the
                same name from the metastore. Given as a kwarg. Default
                is error out if table already exists.

        Returns:
            pyspark.sql.DataFrame: DataFrame associated with the created
            table.
        """
        overwrite_existing_table = (
            options.pop('mode', '').lower() == 'overwrite' and
            self.has_table(table_name)
        )

        def _append_unique_suffix(*args):
            return '__'.join(args + (uuid.uuid4().hex, ))

        if overwrite_existing_table:
            new_table_name = _append_unique_suffix(table_name, 'new')
        else:
            new_table_name = table_name

        if hasattr(self._spark.catalog, 'createTable'):
            createTable = self._spark.catalog.createTable
        else:  # before Spark 2.2
            createTable = self._spark.catalog.createExternalTable

        df = createTable(
            new_table_name,
            path=path,
            source=source,
            schema=schema,
            **options
        )

        if overwrite_existing_table:
            old_table_name = _append_unique_suffix(table_name, 'old')
            self.rename_table(table_name, old_table_name)
            self.rename_table(new_table_name, table_name)
            self.drop_table(old_table_name)

        return df

    def drop_table(self, table_name, checkfirst=True):
        """Drop table from the metastore.

        Note:
            Follow the official documentation to understand `DROP TABLE` semantic.
            https://cwiki.apache.org/confluence/display/Hive/LanguageManual+DDL\
            #LanguageManualDDL-DropTable

        Args:
            table_name (str): A table name.
            checkfirst (bool): Only issue DROPs for tables that are presented in the database.
        """
        db_name = get_db_name(table_name)
        if checkfirst and not self.has_database(db_name):
            return

        drop_statement = 'DROP TABLE IF EXISTS' if checkfirst else 'DROP TABLE'
        return self._spark.sql(
            '{} {}'.format(drop_statement, table_name)
        )

    def has_table(self, table_name):
        """Check if table is available in the metastore.

        Args:
            table_name (str): A table name.

        Returns:
            bool
        """
        db_name = get_db_name(table_name)
        rel_table_name = get_table_name(table_name)

        if not self.has_database(db_name):
            return False

        for table in self._spark.catalog.listTables(db_name):
            if table.name == rel_table_name:
                return True

        return False

    def has_database(self, db_name):
        """Check if database exists in the metastore.

        Args:
            db_name (str): Database name.

        Returns:
            bool
        """
        if not db_name:
            return True

        for db in self._spark.catalog.listDatabases():
            if db_name == db.name:
                return True

        return False

    def rename_table(self, old_table_name, new_table_name):
        """Rename table in the metastore.

        Note:
            Follow the official documentation to understand `ALTER TABLE` semantic.
            https://cwiki.apache.org/confluence/display/Hive/LanguageManual+DDL\
            #LanguageManualDDL-RenameTable

        Args:
            old_table_name (str): The current table name.
            new_table_name (str): An expected table name.
        """
        self._spark.sql('ALTER TABLE {} RENAME TO {}'.format(old_table_name, new_table_name))

    def get_table_property(self, table_name, property_name, to_type=None):
        """Get table property value from the metastore.

        Args:
            table_name (str): A table name. Might contain a db name.
                E.g. "my_table" or "default.my_table".
            property_name (str): A property name to read value for.
            to_type (function): Cast value to the given type. E.g. `int` or `float`.

        Returns:
            Any
        """
        if not to_type:
            to_type = str

        df = self._spark.sql("SHOW TBLPROPERTIES {}('{}')".format(table_name, property_name))
        prop_val = df.collect()[0].value.strip()

        if 'does not have property' not in prop_val:
            return to_type(prop_val)

    def get_table_properties(self, table_name):
        """Get table properties from the metastore.

        Args:
            table_name (str): A table name.

        Returns:
            dict[str,str]: Key/value for properties.
        """
        rows = self._spark.sql('SHOW TBLPROPERTIES {}'.format(table_name)).collect()
        return {row.key: row.value for row in rows}

    def set_table_property(self, table_name, property_name, value):
        """Set value for table property.

        Args:
            table_name (str): A table name.
            property_name (str): A property name to set value for.
            value (Any): Will be automatically casted to string.
        """
        self._spark.sql("ALTER TABLE {} SET TBLPROPERTIES ('{}'='{}')".format(
            table_name, property_name, value
        ))


def get_db_name(table_name):
    """Get database name from full table name."""
    parts = table_name.split('.', 1)
    if len(parts) == 1:
        return None
    else:
        return parts[0]


def get_table_name(table_name):
    """Get table name from full table name."""
    parts = table_name.split('.', 1)
    return parts[-1]
