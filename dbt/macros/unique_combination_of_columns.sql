{#
    Fails with one row per duplicated key combination. One test per model on purpose: a single
    UNION ALL across several large bridges made DuckDB hold every hash table in the same memory
    budget and spill, which ran ~2.5x slower than the same checks as separate queries.
#}
{% test unique_combination_of_columns(model, combination) %}
select
    {{ combination | join(', ') }},
    count(*) as row_count
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}
