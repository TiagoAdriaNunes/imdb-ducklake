{% test relationships_with_max_orphan_ratio(
    model,
    column_name,
    to,
    field,
    max_orphan_ratio=0.0001
) %}
with child_keys as (
    select {{ adapter.quote(column_name) }} as child_key
    from {{ model }}
    where {{ adapter.quote(column_name) }} is not null
),

metrics as (
    select
        count(*) as child_rows,
        count(*) filter (where parent.{{ adapter.quote(field) }} is null) as orphan_rows
    from child_keys
    left join {{ to }} as parent
        on child_keys.child_key = parent.{{ adapter.quote(field) }}
)

select
    child_rows,
    orphan_rows,
    orphan_rows::double / nullif(child_rows, 0) as orphan_ratio,
    {{ max_orphan_ratio }}::double as maximum_orphan_ratio
from metrics
where orphan_rows > child_rows * {{ max_orphan_ratio }}
{% endtest %}
