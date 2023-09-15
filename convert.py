from pathlib import Path
import re
import pandas as pd
import shutil
import subprocess

# Path to where the MetacatUI files are located
metacatui_dir = "path/to/metacatui"
metacatui_dir = Path(metacatui_dir)

# Path to where the converted files will be saved
output_dir = "./metacatui-es6/"
output_dir = Path(output_dir)

# Patterns for finding the files to convert
patterns = {
    "view": "src/**/views/**/*.js",
    "model": "src/**/models/**/*.js",
    "collection": "src/**/collections/**/*.js",
    "router": "src/**/routers/**/*.js",
    "test": "tests/**/*.js",
    "config": "src/**/config.js",
    "other": "src/**/*.js",  # This is the catch-all pattern
}
# Ignore manually imported dependencies and the code for the DataONE website
ignore_patterns = ["src/components/**/*.js", "**/d1website.min.js"]


# Create a copy of the MetacatUI directory that we'll edit
shutil.copytree(metacatui_dir, output_dir, ignore=shutil.ignore_patterns(".git"))

# Init a new git repo in the output directory so we can inspect the changes
subprocess.run(["git", "init"], cwd=output_dir)
subprocess.run(["git", "add", "."], cwd=output_dir)
subprocess.run(
    ["git", "commit", "-m", "Original files before conversion"], cwd=output_dir
)

## ----- Find files ----- ##


def get_theme(path):
    """Identifies the theme of a file based on the path."""
    parts = path.split("/")
    if "themes" in parts:
        theme_index = parts.index("themes")
        return parts[theme_index + 1]
    else:
        return None


def filter_files(files, ignore, visited):
    """Filters a list of files based on the ignore list and visited list."""
    filtered = []
    for f in files:
        if f not in visited and f not in ignore:
            visited.add(f)
            filtered.append(f)
    return filtered

# Track the paths, categories, and themes of the files we visit
visited_files = set()
paths = []
categories = []
themes = []
ignore_files = set()
[ignore_files.update(output_dir.glob(p)) for p in ignore_patterns]

for category, pattern in patterns.items():
    all_files = list(output_dir.glob(pattern))
    filtered_files = filter_files(all_files, ignore_files, visited_files)
    for f in filtered_files:
        paths.append(f)
        categories.append(category)
        themes.append(get_theme(str(f)))

## ----- Manipulate files ----- ##

# IMPORTS


def remove_comments(text):
    """Remove JS comments (//) from text."""
    # Find // until the end of the line (/n or /r or /r/n)
    pattern = r"\/\/.*?[\n\r]"
    compiled_pattern = re.compile(pattern, re.DOTALL)
    # Find for print statements
    matches = compiled_pattern.findall(text)
    for match in matches:
        print(f"Removing comment: {match}")
        # Remove the comments
        text = text.replace(match, "")
    return text


def remove_metacatui_root(text):
    """Remove MetacatUI.root from import paths."""
    mc_pattern = r'["\']?\s*\+?\s*MetacatUI\.root\s*\+?\s*["\']?'
    compiled_pattern = re.compile(mc_pattern, re.DOTALL)
    matches = compiled_pattern.findall(text)
    for match in matches:
        print(f"Removing MetacatUI.root: {match}")
        text = text.replace(match, "")
    return text


def is_text(dep):
    """Checks if a dependency is a text file."""
    txt_ext = [".js", ".html", ".css", ".json", ".txt"]
    ext = Path(dep).suffix
    if ext in txt_ext:
        return True
    return False


def parse_dependencies(dependencies):
    """Parses the dependencies string into a list of dependencies."""
    dependencies = remove_comments(dependencies)
    dependencies = remove_metacatui_root(dependencies)
    dep_pattern = r'["\'](.+?)["\']'
    dep_match = re.findall(dep_pattern, dependencies)
    return dep_match


def parse_parameters(parameters):
    """Parses the parameters string into a list of parameters."""
    parameters = remove_comments(parameters)
    param_match = [x.strip() for x in parameters.split(",")]
    return param_match


def find_require_define_text(js_text):
    """Finds the requireJS define statement at the top of the file."""
    pattern = "define\s*\(\s*\[([^\]]+)\]\s*,\s*function\s*\(([^)]+)\)\s*\{"
    compiled_pattern = re.compile(pattern, re.DOTALL)
    match = compiled_pattern.search(js_text)
    if match is None:
        return None
    entire_match = match.group(0)
    dependencies = match.group(1)
    parameters = match.group(2)
    return {
        "match": entire_match,
        "dependencies": parse_dependencies(dependencies),
        "parameters": parse_parameters(parameters),
    }


def write_import_statements(dependencies, parameters):
    """Writes the import statements for the dependencies and parameters."""
    # Check that we have the same number of dependencies and parameters
    if len(dependencies) != len(parameters):
        print("Warning: number of dependencies and parameters do not match")
    import_statements = ""
    # Use the longer range to make sure we get all the dependencies
    length = max(len(dependencies), len(parameters))
    ignored_deps = []
    ignored_params = []
    for i in range(length):
        try:
            dep = dependencies[i]
        except IndexError:
            dep = None
        try:
            param = parameters[i]
        except IndexError:
            param = None
        if dep is None:
            print(f"Warning: parameter {param} has no dependency")
            ignored_deps.append(dep)
            continue
        if param is None:
            print(f"Warning: dependency {dep} has no parameter")
            ignored_params.append(param)
            continue
        if is_text(dep):
            # Check for and remove the !text prefix
            if dep.startswith("text!"):
                dep = dep[5:]
        statement = f"import {param} from '{dep}';\n"
        import_statements += statement
    return {
        "text": import_statements,
        "ignored_dependencies": ignored_deps,
        "ignored_parameters": ignored_params,
    }


# EXPORTS


def find_return_text(js_text):
    """Finds the return statement at the end of the file."""
    js_text_standard = standardize_return_text(js_text)
    js_text = js_text_standard["text"]
    export_name = js_text_standard["export_name"]
    pattern = r"return\s*([a-zA-Z0-9_]+)\s*;?\s*\}\s*\);?"
    compiled_pattern = re.compile(pattern, re.DOTALL)
    match = compiled_pattern.search(js_text)
    if match is not None:
        return {"match": match.group(0), "export_name": match.group(1)}
    elif export_name:
        return {"match": None, "export_name": export_name}
    else:
        return None


def standardize_return_text(js_text):
    """
    Convert statements in the format `return Backbone...extend({})` to
    `var ExportName = Backbone...extend({})
    """
    class_name_match = re.search(r"@class\s+(\w+)", js_text)
    new_text = js_text
    class_name = None

    # If the class name is found
    if class_name_match:
        class_name = class_name_match.group(1)

        # Regex pattern to search for 'return Backbone.{anything}extend('
        pattern = r"return\s+?Backbone\.(.*?)extend\("

        # Don't continue if a match is not found for the pattern
        if re.search(pattern, js_text):

            # The replacement string using the found class name
            replacement = f"var {class_name} = Backbone.\\1extend("

            # Use re.sub to find and replace the string
            new_text = re.sub(pattern, replacement, js_text)
            new_text = remove_last_closing_bracket(new_text)

    return {"text": new_text, "export_name": class_name}


def remove_last_closing_bracket(text):
    """Removes the last } and ) characters from the text."""
    pattern = r"\}\s*\)\s*;?\s*$"
    modified_text = re.sub(pattern, "", text, count=1, flags=re.DOTALL)
    return modified_text


def write_export_statement(export_name):
    """Writes the export statement."""
    return f"export default {export_name};\n"


def require_to_import_export(js_text):
    """Converts the requireJS define statement to an import/export statement."""

    new_text = js_text
    errors = []
    props = {
        "original_text": js_text,
        "dependencies": [],
        "parameters": [],
        "export_name": None,
        "new_text": None,
        "errors": [],
        "ignored_dependencies": [],
        "ignored_parameters": [],
    }

    # Find the require/define statement
    define = find_require_define_text(js_text)

    if define is None:
        errors.append("No require/define statement found")
    else:
        props["dependencies"] = define["dependencies"]
        props["parameters"] = define["parameters"]
        # Remove the define statement from the text
        new_text = new_text.replace(define["match"], "")
        # Write the import statements
        import_text = write_import_statements(
            define["dependencies"], define["parameters"]
        )
        if import_text is None:
            errors.append("Error writing import statements")
        else:
            # Add the import statements to the top of the file
            new_text = import_text["text"] + new_text
            props["ignored_dependencies"] = import_text["ignored_dependencies"]
            props["ignored_parameters"] = import_text["ignored_parameters"]

    # Find the return statement

    return_statement = find_return_text(new_text)

    if return_statement is None:
        errors.append("No return statement found")
    else:
        props["export_name"] = return_statement["export_name"]
        # Remove the return statement from the text.
        if return_statement["match"]:
            new_text = new_text.replace(return_statement["match"], "")
        # Write the export statement
        export_text = write_export_statement(return_statement["export_name"])
        if export_text is None:
            errors.append("Error writing export statement")
        else:
            # Add the export statement to the end of the file
            new_text = new_text + export_text

    props["new_text"] = new_text
    props["errors"] = errors

    return props


## ----- Write files ----- ##

props = []

for p in paths:
    with open(p, "r") as f:
        js_text = f.read()
        converted = require_to_import_export(js_text)
        props.append(converted)

df = pd.DataFrame(
    {
        "path": paths,
        "category": categories,
        "theme": themes,
        "new_text": [p["new_text"] for p in props],
        "original_text": [p["original_text"] for p in props],
        "dependencies": [p["dependencies"] for p in props],
        "parameters": [p["parameters"] for p in props],
        "num_dependencies": [len(p["dependencies"]) for p in props],
        "num_parameters": [len(p["parameters"]) for p in props],
        "ignored_dependencies": [p["ignored_dependencies"] for p in props],
        "ignored_parameters": [p["ignored_parameters"] for p in props],
        "errors": [p["errors"] for p in props],
        "export_name": [p["export_name"] for p in props],
    }
)


# Write the new files
for i, row in df.iterrows():
    path = row["path"]
    new_text = row["new_text"]
    with open(path, "w") as f:
        f.write(new_text)

# Save a record of the changes
df = df.drop(columns=["new_text", "original_text"])
df.to_csv("record-files-edited.csv", index=False)
