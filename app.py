from flask import Flask, request, jsonify
from flask_cors import CORS
from diff_match_patch import diff_match_patch
import requests
from itertools import zip_longest
from difflib import SequenceMatcher

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:3000", "http://127.0.0.1:3000", "https://fit.neu.edu.vn", "https://courses.neu.edu.vn",
                        "https://courses-omega-flax.vercel.app"],
        "methods": ["OPTIONS", "POST", "GET"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

def extract_text_from_item(item):
    """Extract text and structure from a document item"""
    if "paragraph" in item:
        if "elements" in item["paragraph"]:
            text = ""
            for element in item["paragraph"]["elements"]:
                if "textRun" in element:
                    text += element["textRun"]["content"]
            if text.strip():
                style = item["paragraph"].get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
                return {
                    "type": "paragraph",
                    "text": text.strip(),
                    "style": style,
                    "original": item
                }
    elif "table" in item:
        return {
            "type": "table",
            "data": item["table"], 
            "original": item
        }
    return None

def compare_text_content(text1, text2):
    """Compare two text strings and return detailed differences"""
    matcher = SequenceMatcher(None, text1, text2)
    result = []
    
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'delete':
            result.append({
                "type": "removed",
                "text": text1[i1:i2],
                "fullText": text1,
                "range": [i1, i2]
            })
        elif op == 'insert':
            result.append({
                "type": "added",
                "text": text2[j1:j2],
                "fullText": text2,
                "range": [j1, j2]
            })
        elif op == 'replace':
            result.append({
                "type": "removed",
                "text": text1[i1:i2],
                "fullText": text1,
                "range": [i1, i2]
            })
            result.append({
                "type": "added",
                "text": text2[j1:j2],
                "fullText": text2,
                "range": [j1, j2]
            })
    
    return result

def compare_tables(table1, table2):
    """Compare two tables and return differences"""
    diffs = []
    
    if len(table1["tableRows"]) != len(table2["tableRows"]):
        return [{"type": "structure", "message": "Different number of rows"}]
        
    for row_idx, (row1, row2) in enumerate(zip(table1["tableRows"], table2["tableRows"])):
        if len(row1["tableCells"]) != len(row2["tableCells"]):
            diffs.append({"type": "structure", "message": f"Different number of cells in row {row_idx}"})
            continue
            
        for cell_idx, (cell1, cell2) in enumerate(zip(row1["tableCells"], row2["tableCells"])):
            text1 = ""
            text2 = ""

            if cell1.get("content"):
                for content in cell1["content"]:
                    if content.get("paragraph") and content["paragraph"].get("elements"):
                        for element in content["paragraph"]["elements"]:
                            if element.get("textRun"):
                                text1 += element["textRun"]["content"]
            
            if cell2.get("content"):
                for content in cell2["content"]:
                    if content.get("paragraph") and content["paragraph"].get("elements"):
                        for element in content["paragraph"]["elements"]:
                            if element.get("textRun"):
                                text2 += element["textRun"]["content"]
            
            if text1 != text2:
                if text1:
                    diffs.append({
                        "type": "removed",
                        "content": {
                            "type": "table",
                            "text": text1,
                            "location": {"row": row_idx, "cell": cell_idx}
                        }
                    })
                if text2:
                    diffs.append({
                        "type": "added",
                        "content": {
                            "type": "table",
                            "text": text2,
                            "location": {"row": row_idx, "cell": cell_idx}
                        }
                    })
    
    return diffs

def diff_content(content1, content2):
    """Compare two document contents and return structured differences"""
    structured_diffs = []
    
    for i, item1 in enumerate(content1):
        processed1 = extract_text_from_item(item1)
        if not processed1:
            continue
            
        # Try to find a matching item in content2
        found_match = False
        for j, item2 in enumerate(content2):
            processed2 = extract_text_from_item(item2)
            if not processed2:
                continue
                
            if processed1["type"] == processed2["type"]:
                if processed1["type"] == "paragraph":
                    if processed1["text"] != processed2["text"]:
                        structured_diffs.append({
                            "type": "removed",
                            "content": processed1
                        })
                        structured_diffs.append({
                            "type": "added",
                            "content": processed2
                        })
                    found_match = True
                    break
                elif processed1["type"] == "table":
                    table_diffs = compare_tables(processed1["data"], processed2["data"])
                    if table_diffs:
                        structured_diffs.extend([{
                            "type": diff["type"],
                            "content": {
                                "type": "table",
                                "data": diff,
                                "original": processed1["original"] if diff["type"] == "removed" else processed2["original"]
                            }
                        } for diff in table_diffs])
                    found_match = True
                    break
                    
        if not found_match:
            structured_diffs.append({
                "type": "removed",
                "content": processed1
            })
    
    for item2 in content2:
        processed2 = extract_text_from_item(item2)
        if not processed2:
            continue
            
        found_in_content1 = False
        for item1 in content1:
            processed1 = extract_text_from_item(item1)
            if processed1 and processed1["type"] == processed2["type"]:
                if processed1["type"] == "paragraph" and processed1["text"] == processed2["text"]:
                    found_in_content1 = True
                    break
                elif processed1["type"] == "table":
                    # Consider tables matching if they have the same structure
                    if len(processed1["data"]["tableRows"]) == len(processed2["data"]["tableRows"]):
                        found_in_content1 = True
                        break
        
        if not found_in_content1:
            structured_diffs.append({
                "type": "added",
                "content": processed2
            })
    
    return structured_diffs

@app.route('/compare', methods=['POST', 'OPTIONS'])
def compare_syllabi():
    if request.method == 'OPTIONS':
        return '', 200
        
    try:
        print("Received comparison request")
        data = request.json
        content1 = data.get('content1', [])
        content2 = data.get('content2', [])

        print(f"Processing comparison of {len(content1)} and {len(content2)} items")

        if not content1 or not content2:
            return jsonify({"error": "Both content1 and content2 are required"}), 400

        diffs = diff_content(content1, content2)
        print(f"Found {len(diffs)} differences")
        
        return jsonify({"diffs": diffs}), 200
        
    except Exception as e:
        print(f"Error in compare_syllabi: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    return "Hello", 200

@app.route('/syllabus-list', methods=['GET'])
def get_syllabus_list():
    try:
        response = requests.get('https://fit.neu.edu.vn/codelab/api/syllabus-list')
        return jsonify(response.json()), 200
    except Exception as e:
        print(f"Error fetching syllabus list: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8017, ssl_context="adhoc")