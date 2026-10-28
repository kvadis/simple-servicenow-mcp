---
name: servicenow-review
description: ServiceNow instance review with the simple-servicenow-mcp tools. Use when the user asks about upgrade readiness, ACL gaps, script or scope audits, an update set before promotion, incident triage, catalog item quality, or querying ServiceNow records.
---

# ServiceNow instance review

The tools come from the simple-servicenow-mcp server. A review is only as good
as its **evidence**: every finding you report points at a record the tools
returned.

## Steps

1. **Pin the instance.** If `servicenow://instance/info` lists more than one
   instance, pass `instance` on every call and name the instance in your answer.
   Done when you know which instance each result came from.
2. **Pick the tool** from [Routing](#routing). Prefer a report tool over
   rebuilding its answer from list calls: the report tools page past the row
   cap and grade severity. A tool missing from your tool list lives in a package
   that isn't loaded; tell the user to set `SN_TOOL_PACKAGES` (for example
   `all`).
3. **Read the report's limits.** `truncated: true` means the result is partial.
   `{"error": ...}` means the call failed: the report tools return errors as
   JSON. `out_of_scope` lists what `upgrade_readiness_review` doesn't check.
   Done when every limit present in a result is stated in your answer.
4. **Report.** Lead with the verdict and the severity counts, then the findings
   from most to least severe. For each finding give the table, record name,
   field and line, the evidence, and the tool's recommendation. Done when every
   finding you mention cites its record.
5. **Change data only when the user asks for that change.** The server starts
   read-only; a refused write names `--read-write`, so pass that on to the user
   rather than retrying. For deletes, call once without `confirm`, show the
   preview, and call again with `confirm=true` after the user agrees.

## Routing

| Job | Tool | Follow-up |
| --- | --- | --- |
| Is a scope ready to upgrade? | `upgrade_readiness_review` | `get_script_body` to show the code around a finding |
| Tables in a scope without ACLs | `audit_acls` | |
| Hardcoded sys_ids and deprecated APIs in a scope | `audit_scope` | `get_script_body` |
| Review an update set before promotion | `summarize_update_set` | `list_update_set_changes` for the deletes and high-risk rows |
| Find update sets | `list_update_sets` | `get_update_set` |
| Triage an incident | `triage_incident` | |
| Catalog item quality | `analyze_catalog_item` | `get_catalog_item_variables` |
| Counts and breakdowns | `count_records`, `aggregate_records` | |
| A table's fields, including inherited ones | `describe_table` | |
| Records from any table | `list_records`, `get_record`, `search_records` | |

## Query gotchas

- **A filter on a field that doesn't exist matches every row.** ServiceNow drops
  the unknown condition without an error. Check field names with
  `describe_table` before filtering on a field you haven't seen in a result.
- `^OR` binds to the condition just before it: `a=1^b=2^ORc=3` means
  `a=1 AND (b=2 OR c=3)`.
- Choice fields filter on the stored value, not the label. Update set states
  are `in progress` (with a space), `complete` and `ignore`.
- `list_catalog_items` takes the category **title**, such as `Hardware`.
- `display_value="true"` returns labels instead of sys_ids for reference
  fields. Keep `"false"` when you'll pass the value to another call.
- List tools return at most 100 rows per call by default. Use `count_records`
  for totals and `offset` to page.
- Empty script fields, such as a UI policy's `script_true`, are left out of
  results: absent means empty.
- Articles built from a knowledge template return an empty `text`.
