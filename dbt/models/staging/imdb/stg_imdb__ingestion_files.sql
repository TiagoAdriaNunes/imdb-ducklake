select
    cast("dataset" as varchar) as dataset,
    cast("file_name" as varchar) as file_name,
    cast("table_name" as varchar) as table_name,
    cast("url" as varchar) as url,
    cast("size_bytes" as bigint) as size_bytes,
    cast("sha256" as varchar) as sha256,
    cast("downloaded_at" as varchar) as downloaded_at,
    cast("batch_id" as varchar) as batch_id,
    cast("etag" as varchar) as etag,
    cast("last_modified" as varchar) as last_modified,
    cast("content_type" as varchar) as content_type,
    cast("_dlt_load_id" as varchar) as dlt_load_id
from {{ source('imdb_raw', 'ingestion_files') }}
