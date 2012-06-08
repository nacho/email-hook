# General Utility Functions used in our Git scripts
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

import os
import sys
from subprocess import Popen
import tempfile
import time

def die(message):
    print >>sys.stderr, message
    sys.exit(1)

# This cleans up our generation code by allowing us to use the same indentation
# for the first line and subsequent line of a multi-line string
def strip_string(str):
    start = 0
    end = len(str)
    if len(str) > 0 and str[0] == '\n':
        start += 1
    if len(str) > 1 and str[end - 1] == '\n':
        end -= 1

    return str[start:end]
