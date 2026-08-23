INSTALL ducklake;
  LOAD ducklake;
  ATTACH 'ducklake:postgres:dbname=''ducklake_catalog'' host=''localhost'' port=5432 user=''imdb'' password=''imdb-local-dev''' AS imdb_lake
    (DATA_PATH 'D:/github/imdb-ducklake/data/ducklake/storage', METADATA_SCHEMA 'imdb_lake', OVERRIDE_DATA_PATH true, READ_ONLY);
  USE imdb_lake;