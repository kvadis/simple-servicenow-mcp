"""MCP Prompts: analytical templates for ServiceNow instance review."""

from __future__ import annotations

from mcp.server.fastmcp.prompts import base

from .server import mcp


@mcp.prompt()
def audit_scope(scope: str) -> list[base.Message]:
    """Audit all scripts in a ServiceNow application scope for anti-patterns and upgrade risks.

    Args:
        scope: The application scope to audit (e.g. "x_myapp")
    """
    return [
        base.UserMessage(
            content=f"""Audit the ServiceNow application scope "{scope}" for code quality and upgrade readiness.

Steps:
1. Use list_business_rules with query "sys_scope.scope={scope}^active=true" to find all business rules in this scope.
2. Use list_script_includes with query "sys_scope.scope={scope}^active=true" to find all script includes.
3. For each script, use get_script_body to retrieve the full source code.
4. Analyze every script for:
   - Deprecated API usage (e.g. GlideRecord.next() in scoped apps, getXMLWait, synchronous GlideHTTPRequest)
   - Hardcoded sys_ids instead of using GlideRecord queries or system properties
   - Missing error handling around GlideRecord operations and REST calls
   - Direct table references that should use table API or abstractions
   - Performance anti-patterns (unbounded GlideRecord queries, queries inside loops, dot-walking in large loops)
   - Upgrade risks (overriding OOB scripts, modifying base system tables)

Output a structured report grouped by severity:
- **Critical**: Issues that will break on upgrade or cause data corruption
- **Warning**: Anti-patterns that hurt maintainability or performance
- **Info**: Style suggestions and minor improvements"""
        )
    ]


@mcp.prompt()
def analyze_catalog(category: str | None = None) -> list[base.Message]:
    """Analyze service catalog items for UX quality and consistency.

    Args:
        category: Optional category title to scope the analysis (analyzes all if omitted)
    """
    scope_text = f'in category "{category}"' if category else "across all categories"
    return [
        base.UserMessage(
            content=f"""Analyze the ServiceNow service catalog {scope_text} for quality and consistency.

Steps:
1. Use list_catalog_categories to get an overview of the catalog structure.
2. Use list_catalog_items{f' with category="{category}"' if category else ""} to list items.
3. For each item, use get_catalog_item_variables to inspect the form variables.
4. Analyze for:
   - Naming consistency across items and variables
   - Description quality (missing, too short, or unclear descriptions)
   - Variable naming conventions and ordering
   - Mandatory field usage (too many or too few required fields)
   - Variable type appropriateness (e.g. using String where Reference would be better)
   - Category organization (items in wrong categories, empty categories)
   - Potential duplicate items

Output a structured report with:
- **Overall catalog health score** (A-F)
- **Per-item recommendations** with specific improvement suggestions
- **Cross-cutting issues** that affect multiple items"""
        )
    ]


@mcp.prompt()
def upgrade_readiness_review(
    scope: str,
    target_version: str | None = None,
) -> list[base.Message]:
    """Comprehensive upgrade-readiness review of a ServiceNow scope.

    Walks every script artefact in the scope, surfaces upgrade-blocking patterns,
    and produces a prioritised migration checklist. Goes broader than audit_scope
    by also pulling client scripts, UI policies, and UI actions.

    Args:
        scope: Application scope to review (e.g. "x_myapp" or "global")
        target_version: Optional target release name (e.g. "Yokohama", "Zurich",
            "Washington DC"). When provided, cross-references known deprecations.
    """
    version_qualifier = (
        f" targeting upgrade to ServiceNow **{target_version}**" if target_version else ""
    )
    version_section = (
        f"### Version-specific deprecations ({target_version})\n"
        f"Cross-reference your findings against the documented deprecations for the "
        f"**{target_version}** release. For each pattern flagged, note explicitly "
        f"whether it is *removed*, *deprecated*, or *discouraged* in that release."
        if target_version
        else ""
    )
    return [
        base.UserMessage(
            content=f"""Perform a comprehensive upgrade-readiness review of ServiceNow scope "{scope}"{version_qualifier}.

## Discovery (gather every scripted artefact)

Paginate each list call until exhausted. Use `verbose=false` to keep payloads tight.

1. `list_business_rules` with query `sys_scope.scope={scope}^active=true`
2. `list_script_includes` with query `sys_scope.scope={scope}^active=true`
3. `list_client_scripts` with query `sys_scope.scope={scope}^active=true`
4. `list_ui_policies` with query `sys_scope.scope={scope}^active=true^scriptISNOTEMPTY`
5. `list_ui_actions` with query `sys_scope.scope={scope}^active=true^scriptISNOTEMPTY`
6. For every record, call `get_script_body(table, sys_id)` to fetch the source.
7. Optionally `describe_table(table)` if you need to confirm referenced column shapes.

## Analysis (per artefact, quote the offending lines)

**🔴 Blocking — must fix before upgrade**
- Synchronous GlideRecord/AJAX: `getXMLWait()`, `getXML()` sync form, legacy `chooseWindow()`
- Removed/legacy client APIs: direct `document.*` / `$j()` / `$$()` DOM access in client scripts
- Hardcoded sys_ids that should be system-property lookups
- Modifications to OOTB records (match against base names in `sys_metadata`)
- Use of `gs.executeNow()` with deprecated signatures

**🟠 Risk — review before upgrade**
- Missing try/catch around GlideRecord ops, REST calls, JSON.parse, scripted REST
- Unbounded GlideRecord queries (no `setLimit()` and no precise filter)
- Queries inside loops (N+1) or dot-walking across reference fields in tight loops
- `current.update()` inside before/after business rules without a guard
- Client scripts assuming legacy form rendering (won't run on Service Portal / Next Experience)

**🟡 Info — modernise opportunistically**
- ES5 syntax where the scope supports ES12 (no `let`/`const`/arrow fns/template literals)
- `gs.log()` instead of `gs.info()/warn()/error()`
- Missing JSDoc on script includes
- Naming inconsistent with scope conventions

{version_section}

## Output

A markdown report with:

1. **Executive summary** — counts per severity, overall verdict (🟢 Green / 🟡 Yellow / 🔴 Red).
2. **Findings table** — one row per finding: `Artefact | Type | Severity | Line | Snippet | Recommendation`.
3. **Migration checklist** — sequenced bullet list grouped by file, ready to paste into a ticket.
4. **Out-of-scope notes** — what you did *not* analyse (flows, ATF tests, REST messages, scheduled jobs, etc.) so a reviewer knows what's still uncovered."""
        )
    ]


@mcp.prompt()
def triage_incident(number: str) -> list[base.Message]:
    """Triage a specific incident: assemble context, summarise, recommend next action.

    Pulls the incident, its work-note/comment journal, and similar resolved
    incidents, then produces a paste-ready triage brief.

    Args:
        number: Incident number (e.g. "INC0010234")
    """
    return [
        base.UserMessage(
            content=f"""Triage incident {number} and recommend the next action.

## Gather

1. `list_incidents` with query `number={number}` and `verbose=true` to pull the full record. Note the `sys_id`, `short_description`, `category`, `subcategory`, `caller_id`, `assigned_to`, `assignment_group`, `priority`, `state`, `opened_at`, and any `close_*` fields.
2. `list_records` on `sys_journal_field` with query `element_id=<sys_id>^elementINwork_notes,comments^ORDERBYDESCsys_created_on`, `fields=sys_created_on,sys_created_by,element,value`, `limit=30` — full conversation history.
3. `search_records` on `incident` using the first 4-6 distinctive words of the short_description as `keyword`, `search_fields=short_description,description`, `limit=10`. Then filter the result to closed/resolved (state in 6,7) and fetch `close_code` + `close_notes` for each match.

## Analyse

- Restate the issue in one sentence.
- Summarise what's been tried (from work notes) and by whom.
- Identify missing diagnostic info (logs, repro steps, affected user count, error messages).
- Match against similar resolved incidents — call out close codes / notes that look applicable.
- Identify the most likely owner based on category/subcategory + currently assigned group.

## Output

A markdown brief:

1. **One-line problem statement**
2. **Status** — Priority, State, Age (in business hours), Last touched (and by whom)
3. **What we know** — bulleted facts
4. **What we tried** — bulleted journal summary, newest first
5. **What's missing** — diagnostic info to request from the caller or assigned group
6. **Likely fix** — drawn from closest historical matches; cite incident numbers
7. **Recommended next action** — exactly one of: `request-info`, `reassign`, `escalate`, `propose-resolution`
8. **Suggested work note** — paste-ready text for the next update on this incident"""
        )
    ]


@mcp.prompt()
def update_set_review(update_set: str) -> list[base.Message]:
    """Risk-aware review of an update set before promotion."""
    return [
        base.UserMessage(
            content=f"""Review update set ``{update_set}`` for promotion readiness.

Run these tools in order:

1. ``summarize_update_set("{update_set}")`` — gets the metadata, total change
   count, distribution by type/action, and pre-flagged high-risk + delete lists.
2. If ``high_risk_changes`` is non-empty, call ``list_update_set_changes`` with
   ``query=type=<one of the high-risk types>`` to drill into each.
3. If ``deletes`` is non-empty, walk every entry — deletes against system tables
   (sys_script, sys_security_acl, sys_dictionary, …) are the highest risk.
4. Spot-check 2-3 representative INSERT_OR_UPDATE changes via
   ``list_update_set_changes(..., verbose=true)`` so you can read the XML payload.

Then write a markdown review with these sections:

**Summary**
- Update set name, state, target application
- Total changes, breakdown by action (INSERT_OR_UPDATE vs DELETE)
- One-line verdict: ✅ low-risk / 🟡 review recommended / 🔴 do not promote

**Risk findings** — for each finding, classify and quote the change:
- 🔴 Critical: deletes against system tables; ACL/role changes that broaden access
- 🟠 High: script/business-rule changes touching production tables
- 🟡 Watch: schema additions (sys_dictionary); UI policy changes on customer-facing forms

**Promotion checklist** — concrete steps before clicking "Move to Complete":
- Tests to run / records to spot-check post-deploy
- Items that should arguably be split into a separate set
- Anyone who should sign off (e.g. security on ACL changes)

Keep findings concrete — quote the change's ``target_name`` and ``type`` instead
of vague language. If ``truncated`` is true on the summary, note that the set
has 500+ changes and a deeper sweep is needed."""
        )
    ]


@mcp.prompt()
def trace_incident_impact(number: str) -> list[base.Message]:
    """End-to-end impact analysis: incident → CIs → relationships → recent changes → KB."""
    return [
        base.UserMessage(
            content=f"""Trace the blast radius and likely root cause for incident ``{number}``.

Execute the following chain — stop early only if a step returns no data, and
say so explicitly.

**1. Anchor the incident**
- ``list_incidents(query="number={number}", verbose=true, limit=1)`` — pull the
  full record (caller, assignment_group, category, subcategory, opened_at,
  short_description, description).
- Capture: ``sys_id``, caller's ``sys_id``, opened timestamp, category.

**2. Surface affected CIs**
- ``list_attachments("incident", <incident sys_id>)`` — note any captured
  logs/configs that could pinpoint the host.
- Look for direct CI references in the incident's ``cmdb_ci`` field (visible in
  step 1). If present, fetch with ``get_ci(<sys_id>)``.
- For each affected CI, fetch ``list_ci_relationships(<sys_id>)`` and capture
  both outgoing (what it runs on / depends on) and incoming (what depends on it).

**3. Recent changes on those CIs**
- ``list_records("change_request", query="cmdb_ci=<ci sys_id>^closed_atONLast 14 days@javascript:gs.beginningOfLast14Days()@javascript:gs.endOfToday()", limit=20)``
  — recent changes touching each CI. The 14-day window catches "we changed
  something on Friday and Monday's broken" patterns.
- For each suspect change, fetch the full record (`get_record("change_request", <sys_id>)`)
  and note ``short_description``, ``implementation_plan``, ``closed_by``,
  ``close_notes``.

**4. Knowledge coverage**
- ``search_knowledge(<keywords from incident short_description>, limit=5)`` — any
  published article that matches the symptom.

**5. Synthesise**

Write a markdown report:

**Incident summary**
- Number, caller, category, short_description, opened_at, current state

**Affected CIs** (one section per CI)
- Name, class, operational_status
- Outgoing relationships (this CI → upstream)
- Incoming relationships (downstream → this CI) — these are who else is impacted

**Suspect changes**
- Quote each: number, scheduled window, who implemented, link sys_id
- Rank by closeness to the incident's opened_at (closest = most suspect)
- Call out any change whose ``close_code`` was "Unsuccessful" or that touched
  the same CI within 24h of the incident

**Related knowledge**
- For each KB hit: number, short_description, why it might apply

**Recommended next step** (one paragraph)
- Whether this looks change-induced, capacity-driven, or novel
- One concrete action: who to ping, what to look at first

Quote sys_ids and timestamps verbatim — vague summaries waste this analysis."""
        )
    ]


@mcp.prompt()
def cross_instance_diff(
    table: str, query: str, instance_a: str, instance_b: str
) -> list[base.Message]:
    """Compare records on the same table across two configured instances."""
    return [
        base.UserMessage(
            content=f"""Compare records on ``{table}`` matching the query ``{query}``
between instance ``{instance_a}`` and instance ``{instance_b}``.

Requires multi-instance mode — both instance names must exist in
``SN_INSTANCES_FILE``. Confirm by reading the ``servicenow://instance/info``
resource first; if either name is missing, stop and report.

**1. Pull both sides**
- ``list_records("{table}", query="{query}", instance="{instance_a}", limit=100)``
- ``list_records("{table}", query="{query}", instance="{instance_b}", limit=100)``

**2. Match records**
- Records are matched by ``sys_id`` when available, falling back to ``name`` or
  ``number`` if ``sys_id``s differ across instances (common for non-promoted
  records).

**3. Classify each match**
- ✅ Identical — same sys_id, no meaningful field differences (ignore
  ``sys_updated_on``, ``sys_mod_count``, ``sys_created_on``)
- 🟡 Drift — same record, different field values; quote each diff as
  ``<field>: <a-value> → <b-value>``
- 🔴 Missing in {instance_b} — present in {instance_a} only
- 🔴 Missing in {instance_a} — present in {instance_b} only

**4. Report**

Write a markdown table with columns: ``identity`` (sys_id or name), ``status``
(emoji + label), ``diffs`` (one-line summary of differing fields, or "—").

Below the table:
- **Promotion candidates**: records present in {instance_a} but missing in
  {instance_b} that would be promoted in an Update Set
- **Drift to investigate**: 🟡 rows where the diff is non-trivial (logic
  changes, not just timestamps)
- **Reverse drift**: 🔴 rows present in {instance_b} but not {instance_a} —
  may indicate hot-fixes that haven't been back-ported

Cap the deep-dive at 25 records — if either side has more, note "results
truncated, narrow the query"."""
        )
    ]


@mcp.prompt()
def knowledge_coverage_report(time_window_days: int = 90) -> list[base.Message]:
    """Identify incident categories that lack supporting KB articles."""
    return [
        base.UserMessage(
            content=f"""Audit the knowledge base for coverage gaps relative to recent
incident traffic (last {time_window_days} days).

**1. Aggregate recent incidents by category**
- ``aggregate_records("incident", query="opened_atONLast {time_window_days} days@javascript:gs.beginningOfLast{time_window_days}Days()@javascript:gs.endOfToday()", group_by="category")``
  — count per category over the window.
- Sort by count descending. Note the top 10 categories — these are the
  highest-volume.

**2. Aggregate published KB by category**
- ``aggregate_records("kb_knowledge", query="workflow_state=published", group_by="kb_category")``
  — published articles per category.

**3. Match category labels**
- ``incident.category`` and ``kb_knowledge.kb_category`` use independent
  taxonomies — match by label, not sys_id. Where labels don't align, say so
  explicitly rather than inventing a mapping.

**4. Spot-check the top gap**
- For the highest-volume incident category that has the *fewest* matching KB
  articles, pull ``list_incidents(query="active=false^category=<that category>^ORDERBYDESCsys_updated_on", limit=10, verbose=true)``
  — read the resolution notes. These are the recurring fixes that should be
  documented.

**5. Report**

Markdown sections:

**Coverage matrix**
- Table with columns: category, incident count ({time_window_days}d), published
  KB count, coverage ratio (kb/incidents), verdict (✅ adequate / 🟡 thin /
  🔴 gap)
- Threshold suggestion: gap if ratio < 0.05 AND incident count >= 20

**Top gap deep-dive**
- Category name + incident volume
- 3-5 representative resolved incidents — quote ``close_notes``
- Proposed article(s) the team should write: title + one-sentence rationale

**Coverage already in good shape**
- Categories where ratio is healthy — don't dwell, just list

Recommendation paragraph — which 1-3 articles would have the highest ROI based
on volume."""
        )
    ]


@mcp.prompt()
def instance_health_check() -> list[base.Message]:
    """Quick operational health check of the ServiceNow instance."""
    return [
        base.UserMessage(
            content="""Perform a quick health check of this ServiceNow instance.

Gather the following metrics:
1. Use count_records on "incident" with query "active=true" to get total active incidents.
2. Use count_records on "incident" with query "active=true^priority=1" to get P1 incident count.
3. Use list_incidents with query "active=true^assigned_toISEMPTY^ORDERBYDESCsys_created_on" limit=5 to get newest unassigned incidents.
4. Use count_records on "change_request" with query "active=true^state!=3^state!=7" to get in-flight change requests.
5. Use list_records on "sys_properties" with query "name=glide.product.build_name" to get instance version.

Present a health summary including:
- **Instance version**
- **Active incidents**: total count and P1 count (flag if P1 > 0)
- **Unassigned incidents**: list the 5 newest with number, short description, and priority
- **In-flight changes**: count of active change requests
- **Recommendations**: any immediate actions needed (e.g. unassigned P1s, high change volume)"""
        )
    ]
