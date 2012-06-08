#!/usr/bin/python
#
# gnome-post-receive-email - Post receive email hook for the GNOME Git repository
#
# Copyright (C) 2008  Owen Taylor
# Copyright (C) 2009  Red Hat, Inc
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, If not, see
# http://www.gnu.org/licenses/.
#
# About
# =====
# This script is used to generate mail to commits-list@gnome.org when change
# are pushed to the GNOME git repository. It accepts input in the form of
# a Git post-receive hook, and generates appropriate emails.
#
# The attempt here is to provide a maximimally useful and robust output
# with as little clutter as possible.
#

import re
import os
import pwd
import sys
from email import Header

script_path = os.path.realpath(os.path.abspath(sys.argv[0]))
script_dir = os.path.dirname(script_path)

sys.path.insert(0, script_dir)

from git import *
from util import die, strip_string as s, start_email, end_email

# When we put a git subject into the Subject: line, where to truncate
SUBJECT_MAX_SUBJECT_CHARS = 100

CREATE = 0
UPDATE = 1
DELETE = 2
INVALID_TAG = 3

# Short name for project
projectshort = None

# Human readable name for user, might be None
user_fullname = None

# Who gets the emails
recipients = None

# map of ref_name => Change object; this is used when computing whether
# we've previously generated a detailed diff for a commit in the push
all_changes = {}
processed_changes = {}

class RefChange(object):
    def __init__(self, refname, oldrev, newrev):
        self.refname = refname
        self.oldrev = oldrev
        self.newrev = newrev
        self.cc = set()

        if oldrev == None and newrev != None:
            self.change_type = CREATE
        elif oldrev != None and newrev == None:
            self.change_type = DELETE
        elif oldrev != None and newrev != None:
            self.change_type = UPDATE
        else:
            self.change_type = INVALID_TAG

        m = re.match(r"refs/[^/]*/(.*)", refname)
        if m:
            self.short_refname = m.group(1)
        else:
            self.short_refname = refname

    # Do any setup before sending email. The __init__ function should generally
    # just record the parameters passed in and not do git work. (The main reason
    # for the split is to let the prepare stage do different things based on
    # whether other ref updates have been processed or not.)
    def prepare(self):
        pass

    # Whether we should generate the normal 'main' email. For simple branch
    # updates we only generate 'extra' emails
    def get_needs_main_email(self):
        return True

    # The XXX in [projectname/XXX], usually a branch
    def get_project_extra(self):
        return None

    # Return the subject for the main email, without the leading [projectname]
    def get_subject(self):
        raise NotImplemenetedError()

    # Write the body of the main email to the given file object
    def generate_body(self, out):
        raise NotImplemenetedError()

    def generate_header(self, out, subject, include_revs=True, oldrev=None, newrev=None, cc=None):
        user = os.environ['USER']
        if user_fullname:
            from_address = "%s <%s@src.gnome.org>" % (Header.Header(user_fullname.decode('utf-8')), user)
        else:
            from_address = "%s@src.gnome.org" % (user)

        if cc is None:
            cc = self.cc

        print >>out, s("""
To: %(recipients)s
Cc: %(cc)s
From: %(from_address)s
Subject: %(subject)s
Keywords: %(projectshort)s
X-Git-Refname: %(refname)s
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit
""") % {
            'recipients': recipients,
            'cc': ','.join(cc),
            'from_address': from_address,
            'subject': Header.Header(subject.decode('utf-8')),
            'projectshort': projectshort,
            'refname': self.refname
       }

        if include_revs:
            if oldrev:
                oldrev = oldrev
            else:
                oldrev = NULL_REVISION
            if newrev:
                newrev = newrev
            else:
                newrev = NULL_REVISION

            print >>out, s("""
X-Git-Oldrev: %(oldrev)s
X-Git-Newrev: %(newrev)s
""") % {
            'oldrev': oldrev,
            'newrev': newrev,
       }

        # Trailing newline to signal the end of the header
        print >>out

    def send_main_email(self):
        if not self.get_needs_main_email():
            return

        extra = self.get_project_extra()
        if extra:
            extra = "/" + extra
        else:
            extra = ""
        subject = "[" + projectshort + extra + "] " + self.get_subject()

        email_out = start_email()

        self.generate_header(email_out, subject, include_revs=True, oldrev=self.oldrev, newrev=self.newrev)
        self.generate_body(email_out)

        end_email()

    # Allow multiple emails to be sent - used for branch updates
    def send_extra_emails(self):
        pass

    def send_emails(self):
        self.send_main_email()
        self.send_extra_emails()

# ========================

# Common baseclass for BranchCreation and BranchUpdate (but not BranchDeletion)
class BranchChange(RefChange):
    def __init__(self, *args):
        RefChange.__init__(self, *args)

    def prepare(self):
        # We need to figure out what commits are referenced in this commit thta
        # weren't previously referenced in the repository by another branch.
        # "Previously" here means either before this push, or by branch updates
        # we've already done in this push. These are the commits we'll send
        # out individual mails for.
        #
        # Note that "Before this push" can't be gotten exactly right since an
        # push is only atomic per-branch and there is no locking across branches.
        # But new commits will always show up in a cover mail in any case; even
        # someone who maliciously is trying to fool us can't hide all trace.

        # Ordering matters here, so we can't rely on kwargs
        branches = git.rev_parse('--symbolic-full-name', '--branches', _split_lines=True)
        detailed_commit_args = [ self.newrev ]

        for branch in branches:
            if branch == self.refname:
                # For this branch, exclude commits before 'oldrev'
                if self.change_type != CREATE:
                    detailed_commit_args.append("^" + self.oldrev)
            elif branch in all_changes and not branch in processed_changes:
                # For branches that were updated in this push but we haven't processed
                # yet, exclude commits before their old revisions
                if all_changes[branch].change_type != CREATE:
                    detailed_commit_args.append("^" + all_changes[branch].oldrev)
            else:
                # Exclude commits that are ancestors of all other branches
                detailed_commit_args.append("^" + branch)

        detailed_commits = git.rev_list(*detailed_commit_args).splitlines()

        self.detailed_commits = set()
        for id in detailed_commits:
            self.detailed_commits.add(id)

        # Find the commits that were added and removed, reverse() to get
        # chronological order
        if self.change_type == CREATE:
            # If someone creates a branch of GTK+, we don't want to list (or even walk through)
            # all 30,000 commits in the history as "new commits" on the branch. So we start
            # the commit listing from the first commit we are going to send a mail out about.
            #
            # This does mean that if someone creates a branch, merges it, and then pushes
            # both the branch and what was merged into at once, then the resulting mails will
            # be a bit strange (depending on ordering) - the mail for the creation of the
            # branch may look like it was created in the finished state because all the commits
            # have been already mailed out for the other branch. I don't think this is a big
            # problem, and the best way to fix it would be to sort the ref updates so that the
            # branch creation was processed first.
            #
            if len(detailed_commits) > 0:
                # Verify parent of first detailed commit is valid. On initial push, it is not.
                parent = detailed_commits[-1] + "^"
                try:
                    validref = git.rev_parse(parent, _quiet=True)
                except CalledProcessError:
                    self.added_commits = []
                else:
                    self.added_commits = rev_list_commits(parent + ".." + self.newrev)
                    self.added_commits.reverse()
            else:
                self.added_commits = []
            self.removed_commits = []
        else:
            self.added_commits = rev_list_commits(self.oldrev + ".." + self.newrev)
            self.added_commits.reverse()
            self.removed_commits = rev_list_commits(self.newrev + ".." + self.oldrev)
            self.removed_commits.reverse()

        # In some cases we'll send a cover email that describes the overall
        # change to the branch before ending individual mails for commits. In other
        # cases, we just send the individual emails. We generate a cover mail:
        #
        # - If it's a branch creation
        # - If it's not a fast forward
        # - If there are any merge commits
        # - If there are any commits we won't send separately (already in repo)

        have_merge_commits = False
        for commit in self.added_commits:
            if commit_is_merge(commit):
                have_merge_commits = True

        self.needs_cover_email = (self.change_type == CREATE or
                                  len(self.removed_commits) > 0 or
                                  have_merge_commits or
                                  len(self.detailed_commits) < len(self.added_commits))

    def get_needs_main_email(self):
        return self.needs_cover_email

    # A prefix for the cover letter summary with the number of added commits
    def get_count_string(self):
        if len(self.added_commits) > 1:
            return "(%d commits) " % len(self.added_commits)
        else:
            return ""

    # Generate a short listing for a series of commits
    # show_details - whether we should mark commit where we aren't going to send
    # a detailed email. (Set the False when listing removed commits)
    def generate_commit_summary(self, out, commits, show_details=True):
        detail_note = False
        for commit in commits:
            if show_details and not commit.id in self.detailed_commits:
                detail = " (*)"
                detail_note = True
            else:
                detail = ""
            print >>out, "  " + commit_oneline(commit) + detail

        if detail_note:
            print >>out
            print >>out, "(*) This commit already existed in another branch; no separate mail sent"

    def send_extra_emails(self):
        total = len(self.added_commits)

        for i, commit in enumerate(self.added_commits):
            if not commit.id in self.detailed_commits:
                continue

            email_out = start_email()

            if self.short_refname == 'master':
                branch = ""
            else:
                branch = "/" + self.short_refname

            total = len(self.added_commits)
            if total > 1 and self.needs_cover_email:
                count_string = ": %(index)s/%(total)s" % {
                    'index' : i + 1,
                    'total' : total
                }
            else:
                count_string = ""

            subject = "[%(projectshort)s%(branch)s%(count_string)s] %(subject)s" % {
                'projectshort' : projectshort,
                'branch' : branch,
                'count_string' : count_string,
                'subject' : commit.subject[0:SUBJECT_MAX_SUBJECT_CHARS]
                }

            # If there is a cover email, it has the X-Git-OldRev/X-Git-NewRev in it
            # for the total branch update. Without a cover email, we are conceptually
            # breaking up the update into individual updates for each commit
            if self.needs_cover_email:
                self.generate_header(email_out, subject, include_revs=False, cc=[])
            else:
                parent = git.rev_parse(commit.id + "^")
                self.generate_header(email_out, subject,
                                     include_revs=True,
                                     oldrev=parent, newrev=commit.id)

            email_out.flush()
            git.show(commit.id, M=True, stat=True, _outfile=email_out)
            email_out.flush()
            git.show(commit.id, p=True, M=True, diff_filter="ACMRTUXB", pretty="format:---", _outfile=email_out)
            end_email()

class BranchCreation(BranchChange):
    def __init__(self, *args):
        BranchChange.__init__(self, *args)

        # Inform required parties in case of official branch creation
        if re.match(r'gnome-[0-9]+-[0-9]+$', self.short_refname):
            self.cc.update((
                'release-team@gnome.org',
                'gnome-doc-list@gnome.org',
                'gnome-i18n@gnome.org',
                '%s@src.gnome.org' % os.environ['USER']
            ))

    def get_subject(self):
        return self.get_count_string() + "Created branch " + self.short_refname

    def generate_body(self, out):
        if len(self.added_commits) > 0:
            print >>out, s("""
The branch '%(short_refname)s' was created.

Summary of new commits:

""") % {
            'short_refname': self.short_refname,
       }

            self.generate_commit_summary(out, self.added_commits)
        else:
            print >>out, s("""
The branch '%(short_refname)s' was created pointing to:

 %(commit_oneline)s

""") % {
            'short_refname': self.short_refname,
            'commit_oneline': commit_oneline(self.newrev)
       }

class BranchUpdate(BranchChange):
    def get_project_extra(self):
        if len(self.removed_commits) > 0:
            # In the non-fast-forward-case, the branch name is in the subject
            return None
        else:
            if self.short_refname == 'master':
                # Not saying 'master' all over the place reduces clutter
                return None
            else:
                return self.short_refname

    def get_subject(self):
        if len(self.removed_commits) > 0:
            return self.get_count_string() + "Non-fast-forward update to branch " + self.short_refname
        else:
            # We want something for useful for the subject than "Updates to branch spiffy-stuff".
            # The common case where we have a cover-letter for a fast-forward branch
            # update is a merge. So we try to get:
            #
            #  [myproject/spiffy-stuff] (18 commits) ...Merge branch master
            #
            last_commit = self.added_commits[-1]
            if len(self.added_commits) > 1:
                return self.get_count_string() + "..." + last_commit.subject[0:SUBJECT_MAX_SUBJECT_CHARS]
            else:
                # The ... indicates we are only showing one of many, don't need it for a single commit
                return last_commit.subject[0:SUBJECT_MAX_SUBJECT_CHARS]

    def generate_body_normal(self, out):
        print >>out, s("""
Summary of changes:

""")

        self.generate_commit_summary(out, self.added_commits)

    def generate_body_non_fast_forward(self, out):
        print >>out, s("""
The branch '%(short_refname)s' was changed in a way that was not a fast-forward update.
NOTE: This may cause problems for people pulling from the branch. For more information,
please see:

 http://live.gnome.org/Git/Help/NonFastForward

Commits removed from the branch:

""") % {
            'short_refname': self.short_refname,
       }

        self.generate_commit_summary(out, self.removed_commits, show_details=False)

        print >>out, s("""

Commits added to the branch:

""")
        self.generate_commit_summary(out, self.added_commits)

    def generate_body(self, out):
        if len(self.removed_commits) == 0:
            self.generate_body_normal(out)
        else:
            self.generate_body_non_fast_forward(out)

class BranchDeletion(RefChange):
    def get_subject(self):
        return "Deleted branch " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The branch '%(short_refname)s' was deleted.
""") % {
            'short_refname': self.short_refname,
       }

# ========================

class AnnotatedTagChange(RefChange):
    def __init__(self, *args):
        RefChange.__init__(self, *args)

    def prepare(self):
        # Resolve tag to commit
        if self.oldrev:
            self.old_commit_id = git.rev_parse(self.oldrev + "^{commit}")

        if self.newrev:
            self.parse_tag_object(self.newrev)
        else:
            self.parse_tag_object(self.oldrev)

    # Parse information out of the tag object
    def parse_tag_object(self, revision):
        message_lines = []
        in_message = False

        # A bit of paranoia if we fail at parsing; better to make the failure
        # visible than just silently skip Tagger:/Date:.
        self.tagger = "unknown <unknown@example.com>"
        self.date = "at an unknown time"

        self.have_signature = False
        for line in git.cat_file(revision, p=True, _split_lines=True):
            if in_message:
                # Nobody is going to verify the signature by extracting it
                # from the email, so strip it, and remember that we saw it
                # by saying 'signed tag'
                if re.match(r'-----BEGIN PGP SIGNATURE-----', line):
                    self.have_signature = True
                    break
                message_lines.append(line)
            else:
                if line.strip() == "":
                    in_message = True
                    continue
                # I don't know what a more robust rule is for dividing the
                # name and date, other than maybe looking explicitly for a
                # RFC 822 date. This seems to work pretty well
                m = re.match(r"tagger\s+([^>]*>)\s*(.*)", line)
                if m:
                    self.tagger = m.group(1)
                    self.date = m.group(2)
                    continue
        self.message = "\n".join(["    " + line for line in message_lines])

    # Outputs information about the new tag
    def generate_tag_info(self, out):

        print >>out, s("""
Tagger: %(tagger)s
Date: %(date)s

%(message)s

""") % {
            'tagger': self.tagger,
            'date': self.date,
            'message': self.message,
       }

        # We take the creation of an annotated tag as being a "mini-release-announcement"
        # and show a 'git shortlog' of the changes since the last tag that was an
        # ancestor of the new tag.
        last_tag = None
        try:
            # A bit of a hack to get that previous tag
            last_tag = git.describe(self.newrev+"^", abbrev='0', _quiet=True)
        except CalledProcessError:
            # Assume that this means no older tag
            pass

        if last_tag:
            revision_range = last_tag + ".." + self.newrev
            print >>out, s("""
Changes since the last tag '%(last_tag)s':

""") % {
                'last_tag': last_tag
      }
        else:
            revision_range = self.newrev
            print >>out, s("""
Changes:

""")
        out.write(git.shortlog(revision_range))
        out.write("\n")

    def get_tag_type(self):
        if self.have_signature:
            return 'signed tag'
        else:
            return 'unsigned tag'

class AnnotatedTagCreation(AnnotatedTagChange):
    def get_subject(self):
        return "Created tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The %(tag_type)s '%(short_refname)s' was created.

""") % {
            'tag_type': self.get_tag_type(),
            'short_refname': self.short_refname,
       }
        self.generate_tag_info(out)

class AnnotatedTagDeletion(AnnotatedTagChange):
    def get_subject(self):
        return "Deleted tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The %(tag_type)s '%(short_refname)s' was deleted. It previously pointed to:

 %(old_commit_oneline)s
""") % {
            'tag_type': self.get_tag_type(),
            'short_refname': self.short_refname,
            'old_commit_oneline': commit_oneline(self.old_commit_id)
       }

class AnnotatedTagUpdate(AnnotatedTagChange):
    def get_subject(self):
        return "Updated tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The tag '%(short_refname)s' was replaced with a new tag. It previously
pointed to:

 %(old_commit_oneline)s

NOTE: People pulling from the repository will not get the new tag.
For more information, please see:

 http://live.gnome.org/Git/Help/TagUpdates

New tag information:

""") % {
            'short_refname': self.short_refname,
            'old_commit_oneline': commit_oneline(self.old_commit_id),
       }
        self.generate_tag_info(out)

# ========================

class LightweightTagCreation(RefChange):
    def get_subject(self):
        return "Created tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The lightweight tag '%(short_refname)s' was created pointing to:

 %(commit_oneline)s
""") % {
            'short_refname': self.short_refname,
            'commit_oneline': commit_oneline(self.newrev)
       }

class LightweightTagDeletion(RefChange):
    def get_subject(self):
        return "Deleted tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The lighweight tag '%(short_refname)s' was deleted. It previously pointed to:

 %(commit_oneline)s
""") % {
            'short_refname': self.short_refname,
            'commit_oneline': commit_oneline(self.oldrev)
       }

class LightweightTagUpdate(RefChange):
    def get_subject(self):
        return "Updated tag " + self.short_refname

    def generate_body(self, out):
        print >>out, s("""
The lightweight tag '%(short_refname)s' was updated to point to:

 %(commit_oneline)s

It previously pointed to:

 %(old_commit_oneline)s

NOTE: People pulling from the repository will not get the new tag.
For more information, please see:

 http://live.gnome.org/Git/Help/TagUpdates
""") % {
            'short_refname': self.short_refname,
            'commit_oneline': commit_oneline(self.newrev),
            'old_commit_oneline': commit_oneline(self.oldrev)
       }

# ========================

class InvalidRefDeletion(RefChange):
    def get_subject(self):
        return "Deleted invalid ref " + self.refname

    def generate_body(self, out):
        print >>out, s("""
The ref '%(refname)s' was deleted. It previously pointed nowhere.
""") % {
            'refname': self.refname,
       }

# ========================

class MiscChange(RefChange):
    def __init__(self, refname, oldrev, newrev, message):
        RefChange.__init__(self, refname, oldrev, newrev)
        self.message = message

class MiscCreation(MiscChange):
    def get_subject(self):
        return "Unexpected: Created " + self.refname

    def generate_body(self, out):
        print >>out, s("""
The ref '%(refname)s' was created pointing to:

 %(newrev)s

This is unexpected because:

 %(message)s
""") % {
            'refname': self.refname,
            'newrev': self.newrev,
            'message': self.message
      }

class MiscDeletion(MiscChange):
    def get_subject(self):
        return "Unexpected: Deleted " + self.refname

    def generate_body(self, out):
        print >>out, s("""
The ref '%(refname)s' was deleted. It previously pointed to:

 %(oldrev)s

This is unexpected because:

 %(message)s
""") % {
            'refname': self.refname,
            'oldrev': self.oldrev,
            'message': self.message
      }

class MiscUpdate(MiscChange):
    def get_subject(self):
        return "Unexpected: Updated " + self.refname

    def generate_body(self, out):
        print >>out, s("""
The ref '%(refname)s' was updated from:

 %(newrev)s

To:

 %(oldrev)s

This is unexpected because:

 %(message)s
""") % {
            'refname': self.refname,
            'oldrev': self.oldrev,
            'newrev': self.newrev,
            'message': self.message
      }

# ========================

def make_change(oldrev, newrev, refname):
    refname = refname

    # Canonicalize
    oldrev = git.rev_parse(oldrev)
    newrev = git.rev_parse(newrev)

    # Replacing the null revision with None makes it easier for us to test
    # in subsequent code

    if re.match(r'^0+$', oldrev):
        oldrev = None
    else:
        oldrev = oldrev

    if re.match(r'^0+$', newrev):
        newrev = None
    else:
        newrev = newrev

    # Figure out what we are doing to the ref

    if oldrev == None and newrev != None:
        change_type = CREATE
        target = newrev
    elif oldrev != None and newrev == None:
        change_type = DELETE
        target = oldrev
    elif oldrev != None and newrev != None:
        change_type = UPDATE
        target = newrev
    else:
        return InvalidRefDeletion(refname, oldrev, newrev)

    object_type = git.cat_file(target, t=True)

    # And then create the right type of change object

    # Closing the arguments like this simplifies the following code
    def make(cls, *args):
        return cls(refname, oldrev, newrev, *args)

    def make_misc_change(message):
        if change_type == CREATE:
            return make(MiscCreation, message)
        elif change_type == DELETE:
            return make(MiscDeletion, message)
        else:
            return make(MiscUpdate, message)

    if re.match(r'^refs/tags/.*$', refname):
        if object_type == 'commit':
            if change_type == CREATE:
                return make(LightweightTagCreation)
            elif change_type == DELETE:
                return make(LightweightTagDeletion)
            else:
                return make(LightweightTagUpdate)
        elif object_type == 'tag':
            if change_type == CREATE:
                return make(AnnotatedTagCreation)
            elif change_type == DELETE:
                return make(AnnotatedTagDeletion)
            else:
                return make(AnnotatedTagUpdate)
        else:
            return make_misc_change("%s is not a commit or tag object" % target)
    elif re.match(r'^refs/heads/.*$', refname):
        if object_type == 'commit':
            if change_type == CREATE:
                return make(BranchCreation)
            elif change_type == DELETE:
                return make(BranchDeletion)
            else:
                return make(BranchUpdate)
        else:
            return make_misc_change("%s is not a commit object" % target)
    elif re.match(r'^refs/remotes/.*$', refname):
        return make_misc_change("'%s' is a tracking branch and doesn't belong on the server" % refname)
    else:
        return make_misc_change("'%s' is not in refs/heads/ or refs/tags/" % refname)

def main():
    global projectshort
    global user_fullname
    global recipients

    # No emails for a repository in the process of being imported
    git_dir = git.rev_parse(git_dir=True, _quiet=True)
    if os.path.exists(os.path.join(git_dir, 'pending')):
        return

    projectshort = get_module_name()

    try:
        recipients=git.config("hooks.mailinglist", _quiet=True)
    except CalledProcessError:
        pass

    if not recipients:
        die("hooks.mailinglist is not set")

    # Figure out a human-readable username
    try:
        entry = pwd.getpwuid(os.getuid())
        gecos = entry.pw_gecos
    except:
        gecos = None

    if gecos != None:
        # Typical GNOME account have John Doe <john.doe@example.com> for the GECOS.
        # Comma-separated fields are also possible
        m = re.match("([^,<]+)", gecos)
        if m:
            fullname = m.group(1).strip()
            if fullname != "":
                user_fullname = fullname

    changes = []

    if len(sys.argv) > 1:
        # For testing purposes, allow passing in a ref update on the command line
        if len(sys.argv) != 4:
            die("Usage: generate-commit-mail OLDREV NEWREV REFNAME")
        changes.append(make_change(sys.argv[1], sys.argv[2], sys.argv[3]))
    else:
        for line in sys.stdin:
            items = line.strip().split()
            if len(items) != 3:
                die("Input line has unexpected number of items")
            changes.append(make_change(items[0], items[1], items[2]))

    for change in changes:
        all_changes[change.refname] = change

    for change in changes:
        change.prepare()
        change.send_emails()
        processed_changes[change.refname] = change

if __name__ == '__main__':
    main()
