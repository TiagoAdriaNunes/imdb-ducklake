{#
    Fails with one row per duplicated key combination. Grouping on a single hash first keeps the
    big hash table small enough not to spill (3.7x faster on bridge_title_credits); only rows whose
    hash repeats are regrouped on the real columns, so collisions can't cause false failures.
    One test per model on purpose: a single UNION ALL across several large bridges made DuckDB
    hold every hash table in the same memory budget and spill.
#}
{% test unique_combination_of_columns(model, combination) %}
{%- set columns = combination | join(', ') -%}
with repeated_hashes as (
    select hash({{ columns }}) as key_hash
    from {{ model }}
    group by key_hash
    having count(*) > 1
)
select
    {{ columns }},
    count(*) as row_count
from {{ model }}
where hash({{ columns }}) in (select key_hash from repeated_hashes)
group by {{ columns }}
having count(*) > 1
{% endtest %}
