# Parameterized Build Workflow

## When to Use
User wants to trigger a Jenkins build that requires parameters (branch picker,
deploy target, version string, boolean flags, etc.). Use this instead of
`workflows/build.md` whenever the job is a parameterized pipeline.

## Steps

1. **Identify the target.** Resolve the user's request to `<folder>/<project>/<branch>`.
   Default branch is `development` unless the user specifies otherwise.

2. **Discover the job's parameters.** Hit the job config endpoint to see what
   parameters the pipeline declares and their default values:

```bash
source ~/.gsd/jenkins.env
curl -sf -u "$JENKINS_USER:$JENKINS_API_TOKEN" \
  "$JENKINS_URL/job/<folder>/job/<project>/job/<branch>/api/json?tree=property[parameterDefinitions[name,type,defaultParameterValue[value],choices]]"
```

The response includes a `parameterDefinitions` array with:
- `name` — the parameter key to send
- `type` — `StringParameterDefinition`, `BooleanParameterDefinition`, `ChoiceParameterDefinition`, etc.
- `defaultParameterValue.value` — the default
- `choices` — allowed values for choice parameters

If the endpoint returns no `parameterDefinitions`, the job is not parameterized
— use `workflows/build.md` instead.

3. **Confirm before triggering.** Always confirm with the user before triggering,
   showing the resolved parameter values:
   > "Trigger build for `<folder>/<project>/<branch>` with `KEY=value` ...?"

   This is an outward-facing action — do not proceed without explicit approval.

4. **Trigger the parameterized build:**

```bash
bash <skill_dir>/scripts/jenkins-api.sh build-with-params <folder> <project> [branch] KEY=value [KEY=value ...]
```

The subcommand form-encodes each `KEY=value` pair and POSTs to
`/buildWithParameters`. Branch is optional and defaults to `development`.
Booleans should be passed as `true`/`false` strings.

5. **Report.** Show the trigger result and link. Poll status as in the
   non-parameterized flow (`workflows/build.md` step 4).

## Notes

- Jenkins returns HTTP 201 when a parameterized build is queued successfully.
- 400/500 responses usually mean a required parameter is missing or has an
  invalid value — re-check the discovery step.
- Values are URL-encoded by the script; spaces and special characters are safe.
- For string parameters that contain `=`, only the first `=` is treated as the
  key/value separator — the rest is part of the value.
