## ADF (Atlassian Document Format)

Jira Cloud rejects plain strings in the `description` field. All descriptions
must be valid ADF version 1 objects.

### Minimum valid document

```json
{
  "version": 1,
  "type": "doc",
  "content": [
    {
      "type": "paragraph",
      "content": [{ "type": "text", "text": "Your text here." }]
    }
  ]
}
```

### Node types used in the script

The `scripts/jira-api.js` script exports these builder functions that the
create workflow uses. **Always build descriptions with these — never raw JSON.**

| Function | Output |
|----------|--------|
| `adf([...blocks])` | Top-level doc wrapper |
| `heading(text, level)` | `h1`–`h6` heading (default `level=2`) |
| `paragraph(...inlines)` | Paragraph containing inline nodes |
| `text(str)` | Plain text inline |
| `bold(str)` | Bold inline |
| `code(str)` | Inline code |
| `codeBlock(content, lang)` | Fenced code block |
| `bulletList(...items)` | Unordered list; items are strings or inline arrays |
| `orderedList(...items)` | Ordered list |
| `rule()` | Horizontal rule |

### Example: bug description

```javascript
const description = adf([
  heading('Summary'),
  paragraph(text('The HTTP client never times out on slow upstream responses.')),

  heading('Details'),
  paragraph(
    code('HttpClient.java:43'),
    text(' calls '),
    code('execute()'),
    text(' with no timeout parameter.')
  ),

  heading('Impact'),
  paragraph(text('Thread pool exhaustion during upstream degradation.')),

  heading('Fix'),
  codeBlock('builder.setConnectTimeout(5000).setSocketTimeout(30000)', 'java'),
]);
```

### Passing description to the script

Write the ADF object to a temp file, then pass the path:

```bash
node -e "
const {adf, heading, paragraph, text} = require('./scripts/jira-api.js');
const d = adf([heading('Summary'), paragraph(text('...'))]);
require('fs').writeFileSync('/tmp/desc.json', JSON.stringify(d));
"
node scripts/jira-api.js create --description-file /tmp/desc.json ...
```

The create workflow handles this automatically.
