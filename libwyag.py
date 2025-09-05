import argparse
import configparser
from datetime import datetime
from fnmatch import fnmatch
import hashlib
from math import ceil
import os
import re
import sys
import zlib

############################################
###### 3. Creating repositories. init ######
############################################

## 3.1. The Repository object
# Main parser
argparser = argparse.ArgumentParser(description="description")

# Subparser. It gets the command alongside wyag
argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True

def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)
    match args.command:
        case "add": cmd_add(args)
        case "cat-file": cmd_cat_file(args)
        case "check-ignore": cmd_check_ignore(args)
        case "checkout": cmd_checkout(args)
        case "commit": cmd_commit(args)
        case "hash-object": cmd_hash_object(args)
        case "init": cmd_init(args)
        case "log": cmd_log(args)
        case "ls-files": cmd_ls_files(args)
        case "ls-tree": cmd_ls_tree(args)
        case "rev-parse": cmd_rev_parse(args)
        case "rm": cmd_rm(args)
        case "show-ref": cmd_show_ref(args)
        case "status": cmd_status(args)
        case "tag": cmd_tag(args)
        case _: print("Bad command.")


# Repository class. Creates a basic repository. Saves working tree, gitdir (within working tree) and configuration
class GitRepository (object):
    """A git repository"""

    worktree = None
    gitdir = None
    conf = None

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception(f"Not a Git repository {path}")

        # Read configuration file in .git/config
        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")

        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception(f"Unsupported repositoryformatversion: {vers}")


# Creates and returns path under a given path.
def repo_path(repo, *path):
    """Compute path under repo's gitdir."""
    return os.path.join(repo.gitdir, *path)


# Returns and optionally create a path to a file
def repo_file(repo, *path, mkdir=False):
    """Same as repo_path, but create dirname(*path) if absent.  For
    example, repo_file(r, \"refs\", \"remotes\", \"origin\", \"HEAD\") will create
    .git/refs/remotes/origin."""

    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path)


#Returns and optionally create a path to a directory.
def repo_dir(repo, *path, mkdir=False):
    """Same as repo_path, but mkdir *path if absent if mkdir."""

    path = repo_path(repo, *path)

    if os.path.exists(path):
        if (os.path.isdir(path)):
            return path
        else:
            raise Exception(f"Not a directory {path}")

    if mkdir:
        os.makedirs(path)
        return path
    else:
        return None


# Creates simple configuration file 
def repo_default_config():
    ret = configparser.ConfigParser()

    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")

    return ret


# Creates new repository at a path. 
# Creates directory structure within git path. 
# Creates files with default syntax
# If repository already exists within the path and contains something, raise exception
def repo_create(path):
    repo = GitRepository(path, True)

    # First, we make sure the path either doesn't exist or is an empty dir.

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception (f"{path} is not a directory!")
        if os.path.exists(repo.gitdir) and os.listdir(repo.gitdir):
            raise Exception (f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)

    assert repo_dir(repo, "branches", mkdir=True)
    assert repo_dir(repo, "objects", mkdir=True)
    assert repo_dir(repo, "refs", "tags", mkdir=True)
    assert repo_dir(repo, "refs", "heads", mkdir=True)

    # .git/description
    with open(repo_file(repo, "description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository. \n")

    # .git/HEAD
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")

    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo


## 3.2. The init command

# Argparse subparser to handle command's argument
argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository")

# Specific subparser. Saves path provided into "args.path". "." by deafult 
argsp.add_argument("path",
                    metavar="directory",
                    nargs="?",
                    default=".",
                    help="Whre to create the repository.")

# Init function. Gets arguments through command line and call repo_create with the path
def cmd_init(args):
    repo_create(args.path)


# Check if repository exists given a path. If true, returns git repository. If false, checks the parent
def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    parent = os.path.realpath(os.path.join(path, ".."))

    if parent == path:
        if required:
            raise Exception("No git directory.")
        else:
            return None

    return repo_find(parent, required)


#############################################
###### 4. Reading and writing objects: ######
###### hash-object and cat-file        ######
#############################################

# Every object share the same storage/retrieval mechanism (serialize/deserialize)
# Other classes will extend this class and implement their own way of reading or writing meaningful data
# Also a default method to create a new empty object is needed
class GitObject (object):

    # Either loads the object from provided data or creates a new empty one
    def __init__(self, data=None):
        if data != None:
            self.deserialize(data)
        else:
            self.init()

    def serialize(self, repo):
        raise Exception("Unimplemented")

    def deserialize(self, repo):
        raise Exception("Unimplemented")

    def init(self):
        pass

# Reading Wyag object
# An object starts with a header that specifies its type: blob, commit, tag or tree (more on that in a second). 
# This header is followed by an ASCII space (0x20), then the size of the object in bytes as an ASCII number, 
# then null (0x00) (the null byte), then the contents of the object.

# Input: repo + SHA string
# Output: Python object (GitTree, GitCommit, GitBlob...)
# Example
# Input: object_read(repo, "abc123")
# Output: GitCommit(b'tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147\nparent 206941306e8a8af65b66eaaaea388a7ae24d49a0\nauthor...\n\nCreate first draft')
def object_read(repo, sha):
    path = repo_file(repo, "objects", sha[0:2], sha[2:]) # path = ".git/objects/ab/c123"

    if not os.path.isfile(path):
        return None

    with open (path, "rb") as f:
        # Decompresse object
        # Example: raw = b'tree 95\x00100644 README.md\x00\xde\xf4V...[20 bytes]100644 main.py\x00\x78\x9a\xbc...[20 bytes]40000 src\x00\x11\x12"...[20 bytes]'
        raw = zlib.decompress(f.read())

        # Now there is something like:
        # b'commit 1086\x00tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147\nparent 
        # 206941306e8a8af65b66eaaaea388a7ae24d49a0
        # \nauthor Thibault Polge <thibault@thb.lt> 
        # 1527025023 +0200\n\nCreate first draft'

        # Find first space to get object type
        x = raw.find(b' ') # Example: x = 6 (position of space)
        fmt = raw[0:x] # Example: raw[0:6] = b'commit'

        # Find null byte to get size
        y = raw.find(b'\x00', x) # Example: y = 11 (positon of \x00)
        size = int(raw[x:y].decode("ascii")) # Example: size = 1086

        # Check size is equal
        if size != len(raw)-y-1: # Example: content_size should be full content minus (null byte plus size information)
            raise Exception(f"Malformed object {sha}: bad length")

        # Pick constructor
        match fmt:
            case b'commit': c=GitCommit
            case b'tree': c=GitTree
            case b'tag': c=GitTag
            case b'blob': c=GitBlob
            case _:
                raise Exception(f"Unknown type {fmt.decode("ascii")} for object {sha}")

        # Return class with content
        # Example: c=GitCommit. return GitCommit(raw[12:end]), only the content of the object
        # GitCommit(b'tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147\nparent 206941306e8a8af65b66eaaaea388a7ae24d49a0\nauthor...\n\nCreate first draft'))
        return c(raw[y+1:]) 

# Writing Wyag object
def object_write(obj, repo=None):
    # First, serialize object. It only contains the data for now
    data = obj.serialize()

    # Build header of the object.
    # fmt = object type
    result = obj.fmt + b' ' + str(len(data)).encode() + b'\x00' + data

    # Compute hash of all the object
    sha = hashlib.sha1(result).hexdigest()

    if repo:
        # Compute path (Creates path)
        path = repo_file(repo, "objects", sha[0:2], sha[2:], mkdir=True)

        # If path exists, compress object and write it there
        if not os.path.exists(path):
            with open(path, 'wb') as f:
                # Compress and write
                f.write(zlib.compress(result))
    return sha

# GitBlob class. It has no format
class GitBlob(GitObject):
    fmt = b'blob'

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


# cat-file command. Prints the raw contents of an object to stdout
# Syntax: wyag cat-file TYPE OBJECT
argsp = argsubparsers.add_parser("cat-file", help="Provide content of repository objects")

argsp.add_argument("type",
                    metavar="type",
                    choices=["blob", "commit", "tag", "tree"],
                    help="Specify the type")

argsp.add_argument("object",
                    metavar="object",
                    help="The object to display")

# Gets current repo (current location) and calls cat_file
def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())

# Reads object from this repo and prints it
def cat_file(repo, obj, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())

# Git has a lot of ways to refer to objects: full hash, small hash, tags..
# This function will be the name resolution function. It will be implemented later
# First implementation
# def object_find(repo, name, fmt=None, follow=True):
#    return name
#
# Second implementation is under section 7

# hash-object command. Reads a file and computes its hash as and object
# If -w flag is passed, stores it in the repository. Just prints its hash otherwise
# Syntax: wyag hash-object [-w] [-t TYPE] FILE
argsp = argsubparsers.add_parser("hash-object", help="Compute object ID and optionially creates a blob from a file")

argsp.add_argument("-w",
                    dest="write",
                    action="store_true",
                    help="Actually write the object into the database")

argsp.add_argument("-t",
                    metavar="type",
                    dest="type",
                    choices=["blob", "commit", "tag", "tree"],
                    default="blob",
                    help="Specify the type")

argsp.add_argument("path",
                    help="Read object from <file>")

# If -w flag is passed, get repo
def cmd_hash_object(args):
    if args.write:
        repo = repo_find()
    else:
        repo = None

    with open(args.path, "rb") as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)

# Reads data type, creates git object, writes git object (either stores it or just prints its sha)
def object_hash(fd, fmt, repo=None):
    data = fd.read()

    match fmt:
        case b'commit' : obj=GitCommit(data)
        case b'tree'   : obj=GitTree(data)
        case b'tag'    : obj=GitTag(data)
        case b'blob'   : obj=GitBlob(data)
        case _: raise Exception(f"Unknown type {fmt}!")

    return object_write(obj, repo)



#############################################
###### 5. Reading commit history: log #######
#############################################
#
## Example of git commit:
#tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147
#parent 206941306e8a8af65b66eaaaea388a7ae24d49a0
#author Thibault Polge <thibault@thb.lt> 1527025023 +0200
#committer Thibault Polge <thibault@thb.lt> 1527025044 +0200
#gpgsig -----BEGIN PGP SIGNATURE-----
#
# iQIzBAABCAAdFiEExwXquOM8bWb4Q2zVGxM2FxoLkGQFAlsEjZQACgkQGxM2FxoL
# kGQdcBAAqPP+ln4nGDd2gETXjvOpOxLzIMEw4A9gU6CzWzm+oB8mEIKyaH0UFIPh
# rNUZ1j7/ZGFNeBDtT55LPdPIQw4KKlcf6kC8MPWP3qSu3xHqx12C5zyai2duFZUU
# wqOt9iCFCscFQYqKs3xsHI+ncQb+PGjVZA8+jPw7nrPIkeSXQV2aZb1E68wa2YIL
# 3eYgTUKz34cB6tAq9YwHnZpyPx8UJCZGkshpJmgtZ3mCbtQaO17LoihnqPn4UOMr
# V75R/7FjSuPLS8NaZF4wfi52btXMSxO/u7GuoJkzJscP3p4qtwe6Rl9dc1XC8P7k
# NIbGZ5Yg5cEPcfmhgXFOhQZkD0yxcJqBUcoFpnp2vu5XJl2E5I/quIyVxUXi6O6c
# /obspcvace4wy8uO0bdVhc4nJ+Rla4InVSJaUaBeiHTW8kReSFYyMmDCzLjGIu1q
# doU61OM3Zv1ptsLu3gUE6GU27iWYj2RWN3e3HE4Sbd89IFwLXNdSuM0ifDLZk7AQ
# WBhRhipCCgZhkj9g2NEk7jRVslti1NdN5zoQLaJNqSwO1MtxTmJ15Ksk3QP6kfLB
## Q52UWybBzpaP9HEd4XnR+HuQ4k2K0ns2KgNImsNvIyFwbpMUyUWLMPimaV1DWUXo
# 5SBjDB/V/W2JBFR+XKHFJeFwYhj7DD/ocsGr4ZMx/lgc8rjIBkI=
# =lgTX
# -----END PGP SIGNATURE-----
#
#Create first draft

# kvlm = Key-Value List with Message.
# 
def kvlm_parse(message, start=0, dct=None):
    if not dct:
        dct = dict()

    # Search for next space and next line
    next_space = message.find(b' ', start)
    next_line = message.find(b'\n', start)

    # Base case
    # =========
    # If newline appears first, assume blank line. Blank line == message coming next and nothing else after that
    # If there's no space at all, returns also -1
    #
    # Store message in the dictionary, with None as the key, and return. 
    if (next_space < 0) or (next_line < next_space):
        assert next_line == start
        dct[None] = message[start+1:]
        return dct

    # Recursive case
    # ==============
    # Read key. Save it for next iteration 
    # Example:
    # Read from the beginning of the message until the first space: first iteration = "tree"
    key = message[start:next_space]

    # end = 0
    # First iteration, end == tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147 <--- Here
    # If next character is ' ', continue
    # Basically the loop continues until a "\n" followed by a space is found
    end = start
    while True:
        end = message.find(b'\n', end+1)
        if message[end+1] != ord(' '): break

    # Grab the value. Also drop the leading space on continuation lines
    # Example
    # First iteration: tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147 
    #                       ^                                       ^
    #                       |                                       |
    #                       |                                       |
    #                   From here                                 To here
    value = message[next_space+1:end].replace(b'\n ', b'\n')

    # Don't overwrite existing data contents
    # If collision:
    # - If type is list, append the value
    # - If type is not list, convert to a list
    if key in dct:
        if type(dct[key]) == list:
            dct[key].append(value)
        else:
            dct[key] = [ dct[key], value ]
    else:
        dct[key] = value

    # Call recursive
    # end + 1 = start of next key
    return kvlm_parse(message, start=end+1, dct=dct)

# Write git commit object
# Write all fields first, then new line, then message, then final line
def kvlm_serialize(kvlm):
    # Git saves everything in bytes. SHA-1 works with bytes. b'' makes ret a bytes literal
    ret = b''

    # Iterate through all keys
    for k in kvlm.keys():
        if k == None: 
            continue
        # Transform key to a list to iterate
        val = kvlm[k]
        if type(val) != list:
            val = [ val ]

        # For every key, return string should be:
        # key + space + value + space + \n. It should always be a space before \n
        for v in val:
            ret += k + b' ' + (v.replace(b'\n', b'\n ')) + b'\n'

    # After all the keys, it comes the message in a new line
    ret += b'\n' + kvlm[None]

    return ret


# GitCommit object
class GitCommit(GitObject):
    fmt=b'commit'

    def deserialize(self, data):
        self.kvlm=kvlm_parse(data)

    def serialize(self):
        return kvlm_serialize(self.kvlm)

    def init(self):
        self.kvlm = dict()


# log command
argsp = argsubparsers.add_parser("log", help="Display history of a give commit.")
argsp.add_argument("commit",
                    default="HEAD",
                    nargs="?",
                    help="Commit to start at.")

def cmd_log(args):
    repo = repo_find()

    print("digraph wyaglog{")
    print(" node[shape=rect]")
    log_graphviz(repo, object_find(repo, args.commit), set()) # set = values with no specific order and no duplicates
    print("}")

def log_graphviz(repo, sha, seen):
    # If commit is already in "seen" (set), already processed. Returns
    if sha in seen:
        return
    seen.add(sha) # Add commit to set otherwise

    # Get commit message
    commit = object_read(repo, sha)
    message = commit.kvlm[None].decode("utf8").strip()
    message = message.replace("\\", "\\\\")
    message = message.replace("\"", "\\\"")

    # If message has more than 1 line, keep only the first (Don't overload log)
    if "\n" in message:
        message = message[:message.index("\n")]

    # Print first part: current commit plus label (same as git). 
    # Example:  
    # c_a05b9176bca8ddc1ee697d3bffa18edcce289cbc [label="a05b917: Section 5 started (previous one). GitCommit object created. kvlm_serialize needs to be implemented"]
    print(f" c_{sha} [label=\"{sha[0:7]}: {message}\"]")

    # Make sure object is a commit. If other object, it should not have kvlm param, so kvlm_parse() would fail
    assert commit.fmt==b'commit'

    # If commit has no parent, it is the initial commit. Return
    if not b'parent' in commit.kvlm.keys():
        return

    # Af parsing with kvlm_parse(), get parents commits (probably a list)
    parents = commit.kvlm[b'parent']

    # If parents is no list, make it a list
    if type(parents) != list:
        parents = [ parents ]

    # Print second part: current commit plus parent commit
    # Call recursive with parent commit
    for p in parents:
        p = p.decode("ascii")
        print(f" c_{sha} -> c_{p};")
        log_graphviz(repo, p, seen)



#############################################
##### 6. Reading commit data: checkout ######
#############################################

# A tree is an array of three-elements tuples
# File mode - SHA-1 - path (relative to the worktree)
# If SHA-1 == blob, path is a file
# If SHA-1 == tree, path is a directory
# 
# Tree are bynary objects. 
# Its format is:
# [mode] space [path] 0x00 [sha-1]
# - [mode] is up to six bytes and is an octal representation of a file mode, stored in ASCII. 
#   For example, 100644 is encoded with byte values 49 (ASCII “1”), 48 (ASCII “0”), 48, 54, 52, 52. 
#   The first two digits encode the file type (file, directory, symlink or submodule), the last four the permissions.
# - It’s followed by 0x20, an ASCII space;
# - Followed by the null-terminated (0x00) path;
# - Followed by the object’s SHA-1 in binary encoding, on 20 bytes.
# 
# Mode        Path                    SHA-1                           
# 100644  .gitignore  894a44cc066a027465cd26d634948d56d13af9af
# 100644      src     6d208e47659a2a10f5f8640e0155d9276a2130a9 

# A leaf is a single path in a tree
class GitTreeLeaf(object):
    def __init__(self, mode, path, sha):
        self.mode = mode
        self.path = path
        self.sha = sha

# Parser to extract a single record
def tree_parse_one(raw, start=0):
    x = raw.find(b' ', start)
    assert x-start == 5 or x-start == 6

    # Read the mode
    mode = raw[start:x]
    if len(mode) == 5:
        mode = b"10" + mode

    # Find NULL terminator
    y = raw.find(b'\x00', x)
    # Read the path
    path = raw[x+1:y]

    # Read the SHA
    raw_sha = int.from_bytes(raw[y+1:y+21], "big")
    # Convert it into an hex string, padded to 40 chars with zeeros if needed
    # Adds 0's to the left until 40 chars are reached. SHA-1 are always 40 char long
    sha = format(raw_sha, "040x")
    # Returns end of tuple position and data
    return y+21, GitTreeLeaf(mode, path.decode("utf8"), sha)


# Real parser. Call parse_one i a loop until all tuples are processed
def tree_parse(raw):
    pos = 0
    max = len(raw)
    ret = list()
    while pos < max:
        pos, data = tree_parse_one(raw, pos)
        ret.append(data)

    return ret


# Now comes the serializer to write trees back
# Git needs CONSISTENT SORTING, so the same content outputs the same SHA-1


# Define sorting rules. 
# Files are sorted using their exact name.
# Directories are sorted as if they had / at the end
def tree_leaf_sort_key(leaf):
    if leaf.mode.startswith(b"10"):
        return leaf.path
    else:
        return leaf.path + "/"


# Sort items using tree_leaf_sort_key function as a transformer, then write them in order
def tree_serialize(obj):
    obj.items.sort(key=tree_leaf_sort_key)
    ret = b''
    # Creates and returns tuple. mode + ' ' + path encoded + null (\x00) + sha to bytes (20)
    for i in obj.items:
        ret += i.mode
        ret += b' '
        ret += i.path.encode("utf8")
        ret += b'\x00'
        sha += int(i.sha, 16)
        ret += sha.to_bytes(20, byteorder="big")
    return ret

# GitTree class
class GitTree(GitObject):
    fmt=b'tree'

    def deserialize(self, data):
        self.items = tree_parse(data)

    def serialize(self):
        return tree_serialize(self)

    def init(self):
        self.items = list()



# ls-tree command
# Sytanx:
# git ls-tree [-r] TREE. Prints content of a tree, recursively with -r flag (only final objects with their full path)
# Example:
# wyag ls-tree abc123
# - 100644 blob file1.txt    file1.txt
#   040000 tree src          src
#   100644 blob README.md    README.md
#
# wyag ls-tree -r abc123
# - 100644 blob file1.txt         file1.txt
#   100644 blob README.md         README.md  
#   100644 blob main.py           src/main.py
#   040000 tree utils             src/utils
#   100644 blob helper.py         src/utils/helper.py


argsp = argsubparsers.add_parser("ls-tree", help="Pretty-print a tree object")
argsp.add_argument("-r",
                    dest="recursive",
                    action="store_true",
                    help="Recurse into sub-trees")

argsp.add_argument("tree",
                    help="A tree-ish object.")

def cmd_ls_tree(args):
    # Returns git repo
    repo = repo_find()
    # Calls real function with arguments
    ls_tree(repo, args.tree, args.recursive)

def ls_tree(repo, ref, recursive=None, prefix=""):
    # Gets object sha
    sha = object_find(repo, ref, fmt=b"tree") # sha = "abc123"

    obj = object_read(repo, sha)

    # obj should be a git tree
    if (obj.fmt != b'tree'):
        raise Exception(f"Object {sha} is not a tree (it's {obj.fmt.decode('ascii')})")

    # obj.items = [
    # GitTreeLeaf(mode=b'100644', path='README.md', sha='def456...'),
    # GitTreeLeaf(mode=b'100644', path='main.py', sha='789abc...'),
    # GitTreeLeaf(mode=b'40000', path='src', sha='111222...')]
    for item in obj.items:
        # item.mode = b'40000'. len = 5. Then type = b'10' = blob
        if len(item.mode) == 5:
            type = item.mode[0:1]
        # item.mode = b'100644'. len = 6. Then type = b'4' = tree
        else:
            type = item.mode[0:2]

        match type: 
            case b'04': type = "tree"
            case b'10': type = "blob" # A regular file.
            case b'12': type = "blob" # A symlink. Blob contents is link target.
            case b'16': type = "commit" # A submodule
            case _: raise Exception(f"Weird tree leaf mode {item.mode}")

        # If not recursive or not a tree, it's a leaf, print
        if not (recursive and type=='tree'):
            print(f"{'0' * (6 - len(item.mode)) + item.mode.decode("ascii")} {type} {item.sha}\t{os.path.join(prefix, item.path)}")
        # It's recursive and tree, recursive
        else:
            ls_tree(repo, item.sha, recursive, os.path.join(prefix, item.path))


# Checkout command
# Instantiates a commit in the worktree
#
# Example
# wyag checkout abc123 /tmp/my_checkout
# tree abc123 contains:
# ├── README.md (blob def456)
# ├── main.py   (blob 789abc) 
# └── src/      (tree 111222)
#     └── utils.py (blob 333444)
#
# Input: commit and directory
# Output: Tree instantiated in a directory in the filesystem
argsp = argsubparsers.add_parser("checkout", help="Checkout a commit inside of a directory")

argsp.add_argument("commit",
                    help="The commit or tree to checkout")

argsp.add_argument("path",
                    help="The EMPTY directory to checkout on.")

def cmd_checkout(args):
    repo = repo_find()

    obj = object_read(repo, object_find(repo, args.commit)) # Obtains "tree abc123"

    # If object is a commit, get its tree
    if obj.fmt == b'commit':
        obj = object_read(repo, obj.kvlm[b'tree'].decode("ascii"))

    # Verify direcotry path exists. 
    # If it doesn't exist, create one
    # If path exist but it's not a directory, raise exception
    # If directory path exists and is not empty, raise Exception
    if os.path.exists(args.path):
        if not os.path.isdir(args.path):
            raise Exception(f"Not a directory {args.path}!")
        if os.listdir(args.path):
            raise Exception(f"Not empty {args.path}!")
    else:
        os.makedirs(args.path)

    # Calls checkout with only the commit content
    # obj.items = [README.md, main.py, src/]
    tree_checkout(repo, obj, os.path.realpath(args.path))

# Creates directory and file structure of a tree object
#
# Input: repo, tree object, path to checkout
# Output: directory structure with all the object from the tree
def tree_checkout(repo, tree, path):
    for item in tree.items: 
        obj = object_read(repo, item.sha) # item.sha = "def456"  or "111222"
        dest = os.path.join(path, item.path) # /tmp/my_checkout/README.me or /tmp/my_checkout/src <-- this is recursive

        if obj.fmt == b'tree':
            os.makedirs(dest) # If tree, make directory
            tree_checkout(repo, obj, dest) # and checkout the rest
        elif obj.fmt == b'blob':
            with open(dest, "wb") as f:
                f.write(obj.blobdata) # If blob, print its data



#############################################
##### 7. Refs, tags and branches ######
#############################################


# To refer to objects, their name is usually used, not their full hexadecimal identifier.
# This is handled by a mechanism called references.
# These live in subdirectories of .git/refs. They are text containing hexadecimal representations of an object's hash.
#
# .git/refs/
# ├── heads/
# │   ├── main          → points to last commit of main branch
# │   └── develop       → points to last commit of develop branch
# ├── remotes/
# │   └── origin/
# │       └── main      → points to last commit of origin/main
# └── tags/
#     └── v1.0          → points to a specific commit (release)
#
# Example: 
# $ cat .git/refs/heads/main
# 6071c08bcb4757d8c89a30d9755d2466cef8c1de ---- SHA-1 of commit
#
# $ cat .git/HEAD
# ref: refs/heads/main ---- indirect reference
# 
#
# Takes a ref name, follow eventual recursive references, and returns a SHA-1 identifier 
#
# Input: repository and reference to an object
# Output: the SHA-1 identifier of an object (or a reference to another reference)
def ref_resolve(repo, ref):
    # Construct path: .git/refs
    path = repo_file(repo, ref)

    if not os.path.isfile(path):
        return None

    # Reads file content and drops final \n. "ref: refs/heads/main"
    with open(path, 'r') as fp:
        data = fp.read()[:-1]
    # If ref points to another ref, return it recursively --> ref_resolve(.git, refs/heads/main)
    if data.startswith("ref: "):
        return ref_resolve(repo, data[5:])
    # Here the data is a SHA-1, return it.
    else:
        return data


# Collects all refs of a nested object and returns them as a dict
# 
# Input: repository and path (optional)
# Output: a string like "refs/remotes/origin/master"
#
# ref_list(., None) ===> refs/remote/origin/master --> assuming there's a "refs" directory in the working repo
def ref_list(repo, path=None):
    if not path:
        path = repo_dir(repo, "refs")
    ret = dict()

    # If path is ".git/refs"
    for f in sorted(os.listdir(path)):
        can = os.path.join(path, f) # can = ".git/refs/heads"
        if os.path.isdir(can):
            ret[f] = ref_list(repo, can) # It's directory. ret["heads"] = ref_list(repo, ".git/refs/heads")
        else:
            ret[f] = ref_resolve(repo, can) # It's a file. ret["main"] = ref_resolve(repo, ".git/refs/heads/main")

    return ret

argsp = argsubparsers.add_parser("show-ref", help="List references.")


# Bridge function for the show_ref command
#
# Gets the current repo and the collections of refs and calls real worker
def cmd_show_ref(args):
    repo = repo_find()
    refs = ref_list(repo)
    show_ref(repo , refs, prefix="refs")

# Prints references recursively
def show_ref(repo , refs, with_hash=True, prefix=""):
    if prefix:
        prefix = prefix + '/' # "refs" -> "refs/"

    # k = "heads", v={'main': a1b2c3...', 'develop': 'f4g5h6...'}"
    for k, v in refs.items():
        # v is a SHA-1, print
        if type(v) == str and with_hash:
            print (f"{v} {prefix}{k}") # "a1b2c3... refs/heads/main"
        # v is a SHA-1, but no hash
        elif type(v) == str:
            print(f"{prefix}{k}") # "refs/heads/main"
        # v is a directory
        # show_ref(repo, {'main': 'a1b2c3...', 'develop': 'f4g5h6...'}, prefix="refs/heads")
        else:
            show_ref(repo, v, with_hash=with_hash, prefix=f"{prefix}{k}")



## Tag Command ##
#
# In git:
# git tag                  # List all tags
# git tag NAME [OBJECT]    # create a new *lightweight* tag NAME, pointing
#                          # at HEAD (default) or OBJECT
# git tag -a NAME [OBJECT] # create a new tag *object* NAME, pointing at
#                          # HEAD (default) or OBJECT
#
#
class GitTag(GitCommit):
    fmt = b'tag'

argsp = argsubparsers.add_parser("tag", help="List and create tags")

argsp.add_argument("-a", 
                    action="store_true",
                    dest="create_tag_object",
                    help="Wheter to create a tag object")

argsp.add_argument("name",
                    nargs="?",
                    help="The new tag's name")

argsp.add_argument("object",
                    default="HEAD",
                    nargs="?",
                    help="The object the new tag will point to")

# Bridge function for tags.
# List or create tags depending on whether or not argument "name" is provided
def cmd_tag(args):
    repo = repo_find()

    if args.name:
        tag_create(repo, args.name, args.object, create_tag_object = args.create_tag_object)
    else:
        refs = ref_list(repo)
        # Just show the ".git/heads/tags" content
        show_ref(repo, refs["tags"], with_hash=False)

def tag_create(repo, name, ref, create_tag_object=False):
    # Get GitObject from object's reference
    sha = object_find(repo, ref) # sha = abc123cd...

    if create_tag_object:
        tag = GitTag()
        tag.kvlm = dict()
        tag.kvlm[b'object'] = sha.encode()
        tag.kvlm[b'type'] = b'commit'
        tag.kvlm[b'tag'] = name.encode()

        tag.kvlm[b'tagger'] = b'Wyag escuderocuestajulio@gmail.com'
        tag.kvlm[None] = b"A tag generated by wyag, which won't let you customize the message!\n"
        tag_sha = object_write(tag, repo)
        # Create the tag reference
        ref_create(repo, "tags/" + name, tag_sha) #ref_create(repo, "tags/v1.0", tag_sha -- GitTag object)
    else:
        # Create lightweigth tag
        ref_create(repo, "tags/" + name, sha) # ref_create(repo, "tags/v1.0", "abc123cd...")

# Creates real file in filesystem to store the tag reference
def ref_create(repo, ref_name, sha):
    # Opens file and writes sha. Now file ".git/refs/tags/v1.0" contains "abc123cd..."
    with open(repo_file(repo, "refs/" + ref_name), 'w') as fp:
        fp.write(sha + "\n")


## Branches ##
#
# Branches are references to commits (tags can refer to any object)
# They live under ".git/refs/heads"
# The branch ref is updated at each commit:
#
# 1. A new commit object is created, with the current branch’s (commit!) ID as its parent;
# 2. The commit object is hashed and stored;
# 3. The branch ref is updated to refer to the new commit’s hash.

# Resolve name to an object hash in repo
def object_resolve(repo, name):
    candidates = list()
    hashRE = re.compile(r"^[0-9A-Fa-f]{4,40}$")

    # If no name is provided, return
    if not name.strip():
        return None

    # Resolves HEAD name and returns SHA-1
    if name == "HEAD":
        return [ ref_resolve(repo, "HEAD") ]

    # If name is of the form of a hash (small or full) --> 5bd254 or 5bd254aa973646fa16f66d702a5826ea14a3eb45
    if hashRE.match(name):
        name = name.lower()
        prefix = name[0:2] # Get first two digits (directory) --> 5b
        path = repo_dir(repo, "objects", prefix, mkdir=False)
        if path: # path exists. There's a directory that starts with prefix --> objects/5b/...
            rem = name[2:] # Get the rest of the hash
            for f in os.listdir(path): # Get all the content within the 5b directory. If string starts with remanent, it's the hash (either small or long)
                if f.startswith(rem):
                    candidates.append(prefix + f)

    as_tag = ref_resolve(repo, "refs/tags/" + name)
    # If name refers to a tag, add tag's sha-1
    if as_tag:
        candidates.append(as_tag)

    as_branch = ref_resolve(repo, "refs/heads/" + name)
    if as_branch: # Did we find a branch?
        candidates.append(as_branch)

    as_remote_branch = ref_resolve(repo, "refs/remotes/" + name)
    if as_remote_branch: # Did we find a remote branch?
        candidates.append(as_remote_branch)

    return candidates
#
#
# Example: wyag ls-tree v1.0 --> looking for a tree but "v1.0" is a tag
# v1.0 --> commit abc123
# abc123 --> tree def456
# tree def456 --> This is the object!
#
# object_find(repo, "v1.0", fmt=b'tree', follow=True)
def object_find(repo, name, fmt=None, follow=True):
    sha = object_resolve(repo, name)

    if not sha:
        raise Exception(f"No such reference {name}.")
    if len(sha) > 1:
        raise Exception("Ambiguous reference {name}: Candidates are:\n - {'\n - '.join(sha)}.")

    sha = sha[0]

    if not fmt:
        return sha

    # Follow chain references until desired object is found
    while True:
        # 1. object_read(repo, "tag_sha_v1.0") --> obj.fmt = b'tag'
        # 2. object_read(repo, "abc123") --> obj.fmt = b'commit'
        # 3. object_read(repo, "def456") --> obj.fmt = b'tree''
        obj = object_read(repo, sha) 

        # 1. b'tag' != b'tree'. continue
        # 2. b'commit' != b'tree'. continue
        # 3. b'tree' == b'tree'. Returns sha "def456"!!
        if obj.fmt == fmt:
            return sha
        if not follow:
            return None

        # 1. obj.fmt = b'tag' --> returns sha = "abc123" (commit)
        if obj.fmt == b'tag':
            sha = obj.kvlm[b'object'].decode("ascii")
        # 2. obj.fmt = b'commit' and fmt = b'tree' --> returns sha = "def456" (tree)
        elif obj.fmt == b'commit' and fmt == b'tree':
            sha = obj.kvlm[b'tree'].decode("ascii")
        else:
            return None


## rev-parse command ##

# Solving references
argsp = argsubparsers.add_parser("rev-parse", help="Parse revision (or other objects) identifiers")

argsp.add_argument("--wyag-type",
                    metavar="type",
                    dest="type",
                    choices=["blob", "commit", "tag", "tree"],
                    default=None,
                    help="Specify the expected type")

argsp.add_argument("name",
                    help="The name to parse")

def cmd_rev_parse(args):
    if args.type:
        fmt = args.type.encode()
    else:
        fmt = None

    repo = repo_find()
    print (object_find(repo, args.name, fmt, follow=True))