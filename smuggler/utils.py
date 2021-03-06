# Copyright (c) 2009 Guilherme Gondim and contributors
#
# This file is part of Django Smuggler.
#
# Django Smuggler is free software under terms of the GNU Lesser
# General Public License version 3 (LGPLv3) as published by the Free
# Software Foundation. See the file README for copying conditions.

import os
import sys
from django.core import serializers
from django.core.management import CommandError
from django.core.management.color import no_style
from django.core.management.commands.dumpdata import Command as DumpData
from django.db import connections, transaction, router
from django.db.utils import DEFAULT_DB_ALIAS
from django.http import HttpResponse
from smuggler.settings import (SMUGGLER_FORMAT, SMUGGLER_INDENT)
try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO


def get_file_list(path):
    file_list = []
    for file_name in os.listdir(path):
        if not os.path.isdir(file_name):
            file_path = os.path.join(path, file_name)
            file_size = os.path.getsize(file_path)
            file_list.append((file_name, '%0.1f KB'%float(file_size/1024.0)))
    file_list.sort()
    return file_list


def save_uploaded_file_on_disk(uploaded_file, destination_path):
    destination = open(destination_path, 'w')
    for chunk in uploaded_file.chunks():
        destination.write(chunk)
    destination.close()


def serialize_to_response(app_labels=[], exclude=[], response=None,
                          format=SMUGGLER_FORMAT, indent=SMUGGLER_INDENT):
    response = response or HttpResponse(mimetype='text/plain')
    # There's some funky output redirecting going on as Django >= 1.5 writes
    # to a wrapped output stream, instead of just returning the dumped output.
    stream = StringIO()  # this is going to be our stdout
    # We need to fake an OutputWrapper as it's only introduced in Django 1.5
    out = lambda: None
    out.write = lambda s: stream.write(s)  # this seems to be sufficient.
    try:
        # Now make sys.stdout our wrapped StringIO instance and start the dump.
        sys.stdout = out
        dumpdata = DumpData()
        dumpdata.stdout = sys.stdout
        dumpdata.stderr = sys.stderr
        output = dumpdata.handle(*app_labels, **{
            'exclude': exclude,
            'format': format,
            'indent': indent,
            'show_traceback': True,
            'use_natural_keys': True
        })
    except CommandError:
        # We expect and re-raise CommandErrors, these contain "user friendly"
        # error messages.
        raise
    else:
        if output:
            response.write(output)
        else:
            response.write(stream.getvalue())
        return response
    finally:
        # Be nice and cleanup!
        sys.stdout = sys.__stdout__


def load_requested_data(data):
    """
    Load the given data dumps and return the number of imported objects.

    Wraps the entire action in a big transaction.

    """
    style = no_style()

    using = DEFAULT_DB_ALIAS
    connection = connections[using]
    cursor = connection.cursor()

    transaction.commit_unless_managed(using=using)
    transaction.enter_transaction_management(using=using)
    transaction.managed(True, using=using)
    
    models = set()
    counter = 0
    try:
        for format, stream in data:
            objects = serializers.deserialize(format, stream)
            for obj in objects:
                model = obj.object.__class__
                if router.allow_syncdb(using, model):
                    models.add(model)
                    counter += 1
                    obj.save(using=using)
        if counter > 0:
            sequence_sql = connection.ops.sequence_reset_sql(style, models)
            if sequence_sql:
                for line in sequence_sql:
                    cursor.execute(line)
    except Exception, e:
        transaction.rollback(using=using)
        transaction.leave_transaction_management(using=using)
        raise e
    transaction.commit(using=using)
    transaction.leave_transaction_management(using=using)
    connection.close()
    return counter
