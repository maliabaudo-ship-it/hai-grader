Grade all .docx files in a Google Drive folder using the HAI corpus quality grader.

## Usage
/grade-drive-folder https://drive.google.com/drive/folders/YOUR_FOLDER_ID

## Steps

### 1. Get the folder URL
If $ARGUMENTS is empty, ask the user to paste a Google Drive folder link.

### 2. Extract the folder ID
Parse the folder ID from the URL. It is the long alphanumeric string after `/folders/`.
Examples:
- `https://drive.google.com/drive/folders/1ABC23xyz` → `1ABC23xyz`
- `https://drive.google.com/drive/u/0/folders/1ABC23xyz` → `1ABC23xyz`
- `https://drive.google.com/drive/u/1/folders/1ABC23xyz?usp=sharing` → `1ABC23xyz`

### 3. Find all .docx files in the folder
Use the Google Drive MCP search_files tool with this query:
```
parentId = 'FOLDER_ID' and mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
```
Use pageSize=50 and paginate with next_page_token until all files are found.
Also search for .doc files (older Word format):
```
parentId = 'FOLDER_ID' and mimeType = 'application/msword'
```
Report how many files were found before proceeding.

### 4. Download and save each file
For each file found:
1. Call download_file_content with the file ID (no exportMimeType needed — these are already .docx)
2. The result is base64-encoded binary content
3. Decode and save to a temp directory using this Python one-liner, substituting FILE_NAME and BASE64_CONTENT:

```bash
python3 -c "
import base64, os
os.makedirs('/tmp/hai_drive_session', exist_ok=True)
with open('/tmp/hai_drive_session/FILE_NAME', 'wb') as f:
    f.write(base64.b64decode('BASE64_CONTENT'))
"
```

Use the actual file name from the Drive metadata (file.name). If a filename contains spaces, keep them — Python handles this fine.

### 5. Run the grader
Once all files are saved, run:
```bash
python3 /Users/malia.baudo/hai-grader/hai_grader.py --local-dir /tmp/hai_drive_session
```

### 6. Present results
Show the full grader summary output. Then give a plain-English interpretation:
- Call out any hard REJECTs and why (meeting notes, PII, minimal content)
- Flag BORDERLINE docs that need human review
- Highlight top ACCEPTs with their occupation and score
- Note any patterns across the batch (e.g. "most docs are Finance, scoring in the 55–70 range")

### 7. Clean up
Delete the temp directory after reporting:
```bash
rm -rf /tmp/hai_drive_session
```

## Notes
- Only .docx and .doc files are graded. PDFs, PowerPoints, and Sheets are skipped.
- If a file fails to download, log the name and skip it rather than stopping entirely.
- Refer to CLAUDE.md in this project for score interpretation, expected ranges by document type, and known grader limitations.
