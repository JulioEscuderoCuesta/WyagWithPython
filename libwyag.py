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
        else
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
        if data != None;
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
def object_read(repo, sha):
    path = repo_file(repo, "objects", sha[0:2], sha[2:])

    if not os.path.isfile(path):
        return None

    with open (path, "rb") as f:
        # Decompresse object
        raw = zlib.decompress(f.read())

        # Find first space to get object type
        x = raw.find(b' ') # Example: x = 6 (position of space)
        fmt = raw[0:x] # Example: raw[0:6] = b'commit'

        # Find null byte to get size
        y = raw.find(b'\x00', x) # Example: y = 11 (positon of \x00)
        size = int(raw[x:y].decode("ascii")) # Example: size = 1086

        # Check size is equal
        if size != len(raw)-y-1: # Example: content_size should be full content minus null byte plus size information
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
        return c(raw[y+1:]) # Example: c=GitCommit. return GitCommit(raw[12:end]), only the content of the object

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
