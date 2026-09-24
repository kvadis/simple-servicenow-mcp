# Tool reference

Every tool, prompt and resource the server exposes. Tools are grouped by the
package that loads them. [Configuration](configuration.md#tool-packages) covers
which packages load by default and how to pick others.

Every tool accepts an optional `instance` argument that selects a named instance
from `SN_INSTANCES_FILE`; omit it to use the default. The tools that change data
(`create_*`, `update_*`, `delete_*`, `add_*_comment`, `resolve_*`, `upload_*`)
refuse unless the server runs with `--read-write`.

## Packages

| Package | Module | What it covers |
| --- | --- | --- |
| [`audit`](#audit) | `tools/audit.py` | Scope, ACL and upgrade-readiness analysis |
| [`core`](#core) | `tools/table.py` | Any table: read, search, count, aggregate, describe, write |
| [`itsm`](#itsm) | `tools/incident.py` | Incidents, including a one-call triage bundle |
| [`scripts`](#scripts) | `tools/script.py` | Business rules, script includes, client scripts, UI policies, UI actions |
| [`catalog`](#catalog) | `tools/catalog.py` | Service catalog browsing and item UX analysis |
| [`cmdb`](#cmdb) | `tools/cmdb.py` | Configuration items and their relationships |
| [`knowledge`](#knowledge) | `tools/knowledge.py` | Knowledge articles |
| [`attachment`](#attachment) | `tools/attachment.py` | Attachments on any record |
| [`update_set`](#update_set) | `tools/update_set.py` | Update sets, their changes, and a risk summary |

## audit

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `upgrade_readiness_review` | `scope`, `target_version?` | read | JSON report: severity-graded findings (blocking / risk / info) across business rules, script includes, client scripts, scripted UI policies and UI actions, each with field, line number, evidence and a recommendation; `severity_counts`; a red / yellow / green `verdict` |
| `audit_acls` | `scope` | read | JSON report: tables in the scope with no ACLs of their own, `blocking` when nothing protects them and `warning` when a parent table's ACLs apply |
| `audit_scope` | `scope` | read | JSON report: hardcoded sys_ids and deprecated APIs in the scope's business rules and script includes, with `truncated` when a page cap was hit |

An unknown scope returns `{"error": ...}` instead of raising.

## core

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_records` | `table`, `query?`, `fields?`, `limit=20`, `offset=0`, `display_value="false"` | read | `list[dict]` |
| `get_record` | `table`, `sys_id`, `fields?` | read | `dict` |
| `search_records` | `table`, `keyword`, `search_fields="short_description,description,name,number"`, `result_fields?`, `limit=20`, `offset=0` | read | `list[dict]` |
| `count_records` | `table`, `query?` | read | `{table, query, count}` |
| `aggregate_records` | `table`, `query?`, `sum_fields?`, `avg_fields?`, `min_fields?`, `max_fields?`, `group_by?`, `having?`, `count=True`, `display_value="false"` | read | `{table, query, group_by, result}` |
| `describe_table` | `table` | read | `list[dict]`: `sys_dictionary` entries for the table and every table it extends |
| `create_record` | `table`, `data`, `skip_validation=False` | create | `dict` |
| `update_record` | `table`, `sys_id`, `data`, `skip_validation=False` | update | `dict` |
| `delete_record` | `table`, `sys_id`, `confirm=False` | destructive | `dict`: a preview unless `confirm=true` |

`create_record` and `update_record` check field names against `sys_dictionary`,
including inherited fields, and report typos before ServiceNow does. Pass
`skip_validation=true` to bypass. If `sys_dictionary` isn't readable, the write
goes through unchecked.

## itsm

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `triage_incident` | `number` | read | JSON: `{incident, journal, similar_incidents}` in one call |
| `list_incidents` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `create_incident` | `short_description`, `description?`, `urgency?`, `impact?`, `category?`, `caller_id?`, `assignment_group?` | create | `dict` |
| `add_incident_comment` | `sys_id`, `comment`, `comment_type="work_notes"` or `"comments"` | append | `dict` |
| `resolve_incident` | `sys_id`, `close_code`, `close_notes`, `close_state="6"` | update | `dict` |

## scripts

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_business_rules` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_script_includes` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_client_scripts` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_ui_policies` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_ui_actions` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_script_body` | `table`, `sys_id` | read | `dict`; for `sys_ui_policy`, `script_true` and `script_false` |

## catalog

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `analyze_catalog_item` | `item_sys_id` | read | JSON with `findings[]`: missing short description, no variables, too many mandatory fields |
| `list_catalog_categories` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_catalog_items` | `category?` (title, e.g. `"Hardware"`), `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_catalog_item_variables` | `item_sys_id` | read | `list[dict]` |

## cmdb

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_cis` | `query?`, `ci_class?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_ci` | `sys_id` | read | `dict` |
| `list_ci_relationships` | `ci_sys_id`, `direction="both"`, `"outgoing"` or `"incoming"`, `limit=50` | read | `{ci_sys_id, direction, outgoing[], incoming[], total}` |
| `find_cis_by_class` | `ci_class`, `query?`, `limit=20`, `offset=0` | read | `list[dict]` |

## knowledge

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_knowledge_articles` | `query?`, `knowledge_base?`, `state="published"`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `search_knowledge` | `keyword`, `limit=10`, `offset=0`, `state="published"` | read | `list[dict]` |
| `get_knowledge_article` | `sys_id` | read | `dict` |

Articles built from a knowledge template keep their body in template fields, so
`text` comes back empty for them.

## attachment

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_attachments` | `table`, `sys_id` | read | `list[dict]` |
| `get_attachment_metadata` | `attachment_sys_id` | read | `dict` |
| `download_attachment` | `attachment_sys_id`, `max_bytes=1000000`, `force_base64=False` | read | `{sys_id, content_type, size_bytes, encoding, content}` |
| `upload_attachment` | `table`, `sys_id`, `file_name`, `content`, `content_type="text/plain"`, `encoding="text"` or `"base64"` | create | `dict` |
| `delete_attachment` | `attachment_sys_id`, `confirm=False` | destructive | `dict`: a preview unless `confirm=true` |

`download_attachment` returns text for `text/*`, JSON, XML, YAML, JavaScript and
shell content, and base64 for everything else. Text that isn't valid UTF-8 falls
back to base64. Files over `max_bytes` are refused.

## update_set

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `summarize_update_set` | `update_set_sys_id` | read | `{update_set, total_changes, by_type, by_action, deletes[], high_risk_changes[], truncated}` |
| `list_update_sets` | `state="in progress"`, `application?`, `query?`, `limit=20`, `offset=0` | read | `list[dict]` |
| `get_update_set` | `sys_id` | read | `{update_set, change_count}` |
| `list_update_set_changes` | `update_set_sys_id`, `query?`, `limit=50`, `offset=0`, `verbose=False` | read | `list[dict]`, without the XML payload unless `verbose=true` |

`list_update_sets` states are `in progress` (with a space), `complete` and
`ignore`; pass `state=None` for all. `summarize_update_set` flags changes to
high-risk tables: `sys_security_acl`, `sys_user_role`, `sys_user_has_role`,
`sys_script`, `sys_script_include`, `sys_db_object`, `sys_dictionary`.

## Prompts

Prompts are multi-step workflows: each one returns instructions that the model
then carries out with the tools above.

| Prompt | Args | What it does |
| --- | --- | --- |
| `upgrade_readiness_review` | `scope`, `target_version` | Starts from the tool's report, then drills into findings and writes a migration checklist |
| `audit_scope` | `scope` | Pulls a scope's business rules and script includes, flags issues, summarises |
| `update_set_review` | `update_set` | Risk review before promotion: summary, high-risk drill-down, promotion checklist. Needs the `update_set` package |
| `triage_incident` | `number` | Triage with an explicit next action: `request-info`, `reassign`, `escalate` or `propose-resolution` |
| `trace_incident_impact` | `number` | Blast radius: incident → caller's CIs → relationships → recent changes → related KB. Needs `cmdb`, `knowledge` and `attachment` |
| `analyze_catalog` | `category?` | Catalog UX audit: missing descriptions, mandatory-field overload, dangling variables |
| `cross_instance_diff` | `table`, `query`, `instance_a`, `instance_b` | Compares the same query on two instances: drift, promotion candidates, reverse drift |
| `knowledge_coverage_report` | `time_window_days=90` | Incident categories vs. published articles; flags gaps and the articles worth writing first |
| `instance_health_check` | none | Active and P1 incidents, unassigned work, changes in flight, version |

A prompt that needs a package is registered only when that package is loaded.

## Resources

Read-only JSON documents a client can pull in for context.

| URI | Contents |
| --- | --- |
| `servicenow://instance/info` | Configured instance(s), auth method and token state. No secrets |
| `servicenow://health` | A one-row read per instance. `{status: "ok", ...}` or `{status: "error", code, message, ...}`; never raises |
| `servicenow://schema/{table_name}` | The table's own `sys_dictionary` entries (use `describe_table` for inherited fields) |
| `servicenow://scope/{scope_name}` | App scope metadata from `sys_scope` |

## Recipes

Package sets for common jobs. Set them with `SN_TOOL_PACKAGES`.

| Job | Packages | Prompts to start from |
| --- | --- | --- |
| Pre-upgrade or pre-promotion review | `core,scripts,update_set,audit` | `upgrade_readiness_review`, `update_set_review`, `audit_scope` |
| Help-desk triage | `core,itsm,knowledge` | `triage_incident`, `instance_health_check` |
| Incident blast radius | `core,itsm,cmdb,knowledge,attachment` | `trace_incident_impact`, `triage_incident` |
| Catalog UX review | `core,catalog` | `analyze_catalog` |
| Drift between instances | `core,scripts,update_set` + `SN_INSTANCES_FILE` | `cross_instance_diff` |
| Knowledge gaps | `core,itsm,knowledge` | `knowledge_coverage_report` |
