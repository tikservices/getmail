#!/usr/bin/env python2.3
'''Classes implementing message filters.

Currently implemented:

'''

__all__ = [
    'FilterSkeleton',
    'Filter_external',
    'Filter_classifier',
    'Filter_TMDA',
]

import os
import tempfile
import types

from getmailcore.exceptions import *
from getmailcore.compatibility import *
from getmailcore.message import *
from getmailcore.utilities import *
from getmailcore.baseclasses import *

#######################################
class FilterSkeleton(ConfigurableBase):
    '''Base class for implementing message-filtering classes.

    Sub-classes should provide the following data attributes and methods:

      _confitems - a tuple of dictionaries representing the parameters the class
                   takes.  Each dictionary should contain the following key,
                   value pairs:
                     - name - parameter name
                     - type - a type function to compare the parameter value
                     against (i.e. str, int, bool)
                     - default - optional default value.  If not preseent, the
                     parameter is required.

      __str__(self) - return a simple string representing the class instance.

      showconf(self) - log a message representing the instance and configuration
                       from self._confstring().

      initialize(self) - process instantiation parameters from self.conf.
                         Raise getmailConfigurationError on errors.  Do any
                         other validation necessary, and set self.__initialized
                         when done.

      _filter_message(self, msg) - accept the message and deliver it, returning
                                   a tuple (exitcode, newmsg, err). exitcode
                                   should be 0 for success, 99 or 100 for
                                   success but drop the message, anything else
                                   for error. err should be an empty string on
                                   success, or an error message otherwise.
                                   newmsg is an email.Message() object
                                   representing the message in filtered form, or
                                   None on error or when dropping the message.

    See the Filter_external class for a good (though not simple) example.
    '''
    def __init__(self, **args):
        ConfigurableBase.__init__(self, **args)
        try:
            self.initialize()
        except KeyError as o:
            raise getmailConfigurationError(
                'missing required configuration parameter %s' % o
            )
        self.log.trace('done\n')

    def filter_message(self, msg, retriever):
        self.log.trace()
        msg.received_from = retriever.received_from
        msg.received_with = retriever.received_with
        msg.received_by = retriever.received_by
        exitcode, newmsg, err = self._filter_message(msg)
        if exitcode in self.exitcodes_drop:
            # Drop message
            self.log.debug('filter %s returned %d; dropping message\n'
                           % (self, exitcode))
            return None
        elif (exitcode not in self.exitcodes_keep):
            raise getmailFilterError('filter %s returned %d (%s)\n'
                                     % (self, exitcode, err))
        elif err:
            if self.conf['ignore_stderr']:
                self.log.info('filter %s: %s\n' % (self, err))
            else:
                raise getmailFilterError(
                    'filter %s returned %d but wrote to stderr: %s\n'
                    % (self, exitcode, err)
                )

        # Check the filter was sane
        if len(newmsg.headers()) < len(msg.headers()):
            if not self.conf.get('ignore_header_shrinkage', False):
                # Warn user
                self.log.warning(
                    'Warning: filter %s returned fewer headers (%d) than '
                        'supplied (%d)\n'
                    % (self, len(newmsg.headers()), len(msg.headers()))
                )

        # Copy attributes from original message
        newmsg.copyattrs(msg)

        return newmsg

#######################################
class Filter_external(FilterSkeleton, ForkingBase):
    '''Arbitrary external filter destination.

    Parameters:

      path - path to the external filter binary.

      unixfrom - (boolean) whether to include a Unix From_ line at the beginning
                 of the message.  Defaults to False.

      arguments - a valid Python tuple of strings to be passed as arguments to
                  the command.  The following replacements are available if
                  supported by the retriever:

                    %(sender) - envelope return path
                    %(recipient) - recipient address
                    %(domain) - domain-part of recipient address
                    %(local) - local-part of recipient address

                  Warning: the text of these replacements is taken from the
                  message and is therefore under the control of a potential
                  attacker. DO NOT PASS THESE VALUES TO A SHELL -- they may
                  contain unsafe shell metacharacters or other hostile
                  constructions.

                  example:

                    path = /path/to/myfilter
                    arguments = ('--demime', '-f%(sender)', '--',
                        '%(recipient)')

      exitcodes_keep - if provided, a tuple of integers representing filter exit
                       codes that mean to pass the message to the next filter or
                       destination.  Default is (0, ).

      exitcodes_drop - if provided, a tuple of integers representing filter exit
                       codes that mean to drop the message.  Default is
                       (99, 100).

      user (string, optional) - if provided, the external command will be run as
                                the specified user.  This requires that the main
                                getmail process have permission to change the
                                effective user ID.

      group (string, optional) -  if provided, the external command will be run
                                with the specified group ID.  This requires that
                                the main getmail process have permission to
                                change the effective group ID.

      allow_root_commands (boolean, optional) - if set, external commands are
                                        allowed when running as root.  The
                                        default is not to allow such behaviour.

      ignore_stderr (boolean, optional) - if set, getmail will not consider the
            program writing to stderr to be an error.  The default is False.
    '''
    _confitems = (
        ConfFile(name='path'),
        ConfBool(name='unixfrom', required=False, default=False),
        ConfTupleOfStrings(name='arguments', required=False, default="()"),
        ConfTupleOfStrings(name='exitcodes_keep', required=False,
                           default="(0, )"),
        ConfTupleOfStrings(name='exitcodes_drop', required=False,
                           default="(99, 100)"),
        ConfString(name='user', required=False, default=None),
        ConfString(name='group', required=False, default=None),
        ConfBool(name='allow_root_commands', required=False, default=False),
        ConfBool(name='ignore_header_shrinkage', required=False, default=False),
        ConfBool(name='ignore_stderr', required=False, default=False),
        ConfInstance(name='configparser', required=False),
    )

    def initialize(self):
        self.log.trace()
        self.conf['command'] = os.path.basename(self.conf['path'])
        if not os.access(self.conf['path'], os.X_OK):
            raise getmailConfigurationError(
                '%s not executable' % self.conf['path']
            )
        if not isinstance(self.conf['arguments'], tuple):
            raise getmailConfigurationError(
                'incorrect arguments format; see documentation (%s)'
                % self.conf['arguments']
            )
        try:
            self.exitcodes_keep = [int(i) for i in self.conf['exitcodes_keep']
                                   if 0 <= int(i) <= 255]
            self.exitcodes_drop = [int(i) for i in self.conf['exitcodes_drop']
                                   if 0 <= int(i) <= 255]
            if not self.exitcodes_keep:
                raise getmailConfigurationError('exitcodes_keep set empty')
            if frozenset(self.exitcodes_keep).intersection(
                frozenset(self.exitcodes_drop)
            ):
                raise getmailConfigurationError('exitcode sets intersect')
        except ValueError as o:
            raise getmailConfigurationError('invalid exit code specified (%s)'
                                            % o)

    def __str__(self):
        self.log.trace()
        return 'Filter_external %s (%s)' % (self.conf['command'],
                                            self._confstring())

    def showconf(self):
        self.log.trace()
        self.log.info('Filter_external(%s)\n' % self._confstring())

    def _filter_command(self, msg, msginfo, stdout, stderr):
        try:
            # Write out message with native EOL convention
            msgfile = tempfile.TemporaryFile()
            msgfile.write(msg.flatten(False, False,
                                      include_from=self.conf['unixfrom']))
            msgfile.flush()
            os.fsync(msgfile.fileno())
            # Rewind
            msgfile.seek(0)
            # Set stdin to read from this file
            os.dup2(msgfile.fileno(), 0)
            # Set stdout and stderr to write to files
            os.dup2(stdout.fileno(), 1)
            os.dup2(stderr.fileno(), 2)
            change_usergroup(None, self.conf['user'], self.conf['group'])
            args = [self.conf['path'], self.conf['path']]
            for arg in self.conf['arguments']:
                arg = expand_user_vars(arg)
                for (key, value) in list(msginfo.items()):
                    arg = arg.replace('%%(%s)' % key, value)
                args.append(arg)
            # Can't log this; if --trace is on, it will be written to the
            # message passed to the filter.
            #self.log.debug('about to execl() with args %s\n' % str(args))
            os.execl(*args)
        except Exception as o:
            # Child process; any error must cause us to exit nonzero for parent
            # to detect it
            self.log.critical('exec of filter %s failed (%s)'
                              % (self.conf['command'], o))
            os._exit(127)

    def _filter_message(self, msg):
        self.log.trace()
        self._prepare_child()
        msginfo = {}
        msginfo['sender'] = msg.sender
        if msg.recipient != None:
            msginfo['recipient'] = msg.recipient
            msginfo['domain'] = msg.recipient.lower().split('@')[-1]
            msginfo['local'] = '@'.join(msg.recipient.split('@')[:-1])
        self.log.debug('msginfo "%s"\n' % msginfo)

        # At least some security...
        if (os.geteuid() == 0 and not self.conf['allow_root_commands']
                and self.conf['user'] == None):
            raise getmailConfigurationError(
                'refuse to invoke external commands as root by default'
            )

        stdout = tempfile.TemporaryFile()
        stderr = tempfile.TemporaryFile()
        childpid = os.fork()

        if not childpid:
            # Child
            self._filter_command(msg, msginfo, stdout, stderr)
        self.log.debug('spawned child %d\n' % childpid)

        # Parent
        exitcode = self._wait_for_child(childpid)

        stdout.seek(0)
        stderr.seek(0)
        err = stderr.read().strip()

        self.log.debug('command %s %d exited %d\n' % (self.conf['command'],
                                                      childpid, exitcode))

        newmsg = Message(fromfile=stdout)

        return (exitcode, newmsg, err)

#######################################
class Filter_classifier(Filter_external):
    '''Filter which runs the message through an external command, adding the
    command's output to the message header.  Takes the same parameters as
    Filter_external.  If the command prints nothing, no header fields are
    added.
    '''
    def __str__(self):
        self.log.trace()
        return 'Filter_classifier %s (%s)' % (self.conf['command'],
                                              self._confstring())

    def showconf(self):
        self.log.trace()
        self.log.info('Filter_classifier(%s)\n' % self._confstring())

    def _filter_message(self, msg):
        self.log.trace()
        self._prepare_child()
        msginfo = {}
        msginfo['sender'] = msg.sender
        if msg.recipient != None:
            msginfo['recipient'] = msg.recipient
            msginfo['domain'] = msg.recipient.lower().split('@')[-1]
            msginfo['local'] = '@'.join(msg.recipient.split('@')[:-1])
        self.log.debug('msginfo "%s"\n' % msginfo)

        # At least some security...
        if (os.geteuid() == 0 and not self.conf['allow_root_commands']
                and self.conf['user'] == None):
            raise getmailConfigurationError(
                'refuse to invoke external commands as root by default'
            )

        stdout = tempfile.TemporaryFile()
        stderr = tempfile.TemporaryFile()
        childpid = os.fork()

        if not childpid:
            # Child
            self._filter_command(msg, msginfo, stdout, stderr)
        self.log.debug('spawned child %d\n' % childpid)

        # Parent
        exitcode = self._wait_for_child(childpid)

        stdout.seek(0)
        stderr.seek(0)
        err = stderr.read().strip()

        self.log.debug('command %s %d exited %d\n' % (self.conf['command'],
                                                      childpid, exitcode))

        for line in [line.strip() for line in stdout.readlines()
                     if line.strip()]:
            # Output from filter can be in any random text encoding and may
            # not even be valid, which causes problems when trying to stick
            # that text into message headers.  Try to decode it to something
            # sane here first.
            line = decode_crappy_text(line)
            msg.add_header('X-getmail-filter-classifier', line)

        return (exitcode, msg, err)

#######################################
class Filter_TMDA(FilterSkeleton, ForkingBase):
    '''Filter which runs the message through TMDA's tmda-filter program
    to handle confirmations, etc.

    Parameters:

      path - path to the external tmda-filter binary.

      user (string, optional) - if provided, the external command will be run
                                as the specified user.  This requires that the
                                main getmail process have permission to change
                                the effective user ID.

      group (string, optional) -  if provided, the external command will be run
                                with the specified group ID.  This requires that
                                the main getmail process have permission to
                                change the effective group ID.

      allow_root_commands (boolean, optional) - if set, external commands are
                                allowed when running as root.  The default is
                                not to allow such behaviour.

      ignore_stderr (boolean, optional) - if set, getmail will not consider the
            program writing to stderr to be an error.  The default is False.

      conf-break - used to break envelope recipient to find EXT.  Defaults
                                to "-".
    '''
    _confitems = (
        ConfFile(name='path', default='/usr/local/bin/tmda-filter'),
        ConfString(name='user', required=False, default=None),
        ConfString(name='group', required=False, default=None),
        ConfBool(name='allow_root_commands', required=False, default=False),
        ConfBool(name='ignore_stderr', required=False, default=False),
        ConfString(name='conf-break', required=False, default='-'),
        ConfInstance(name='configparser', required=False),
    )

    def initialize(self):
        self.log.trace()
        self.conf['command'] = os.path.basename(self.conf['path'])
        if not os.access(self.conf['path'], os.X_OK):
            raise getmailConfigurationError(
                '%s not executable' % self.conf['path']
            )
        self.exitcodes_keep = (0, )
        self.exitcodes_drop = (99, )

    def __str__(self):
        self.log.trace()
        return 'Filter_TMDA %s' % self.conf['command']

    def showconf(self):
        self.log.trace()
        self.log.info('Filter_TMDA(%s)\n' % self._confstring())

    def _filter_command(self, msg, stdout, stderr):
        try:
            # Write out message with native EOL convention
            msgfile = tempfile.TemporaryFile()
            msgfile.write(msg.flatten(True, True, include_from=True))
            msgfile.flush()
            os.fsync(msgfile.fileno())
            # Rewind
            msgfile.seek(0)
            # Set stdin to read from this file
            os.dup2(msgfile.fileno(), 0)
            # Set stdout and stderr to write to files
            os.dup2(stdout.fileno(), 1)
            os.dup2(stderr.fileno(), 2)
            change_usergroup(None, self.conf['user'], self.conf['group'])
            args = [self.conf['path'], self.conf['path']]
            # Set environment for TMDA
            os.environ['SENDER'] = msg.sender
            os.environ['RECIPIENT'] = msg.recipient
            os.environ['EXT'] = self.conf['conf-break'].join(
                '@'.join(msg.recipient.split('@')[:-1]).split(
                    self.conf['conf-break']
                )[1:]
            )
            self.log.trace('SENDER="%(SENDER)s",RECIPIENT="%(RECIPIENT)s"'
                           ',EXT="%(EXT)s"' % os.environ)
            self.log.debug('about to execl() with args %s\n' % str(args))
            os.execl(*args)
        except Exception as o:
            # Child process; any error must cause us to exit nonzero for parent
            # to detect it
            self.log.critical('exec of filter %s failed (%s)'
                              % (self.conf['command'], o))
            os._exit(127)

    def _filter_message(self, msg):
        self.log.trace()
        self._prepare_child()
        if msg.recipient == None or msg.sender == None:
            raise getmailConfigurationError(
                'TMDA requires the message envelope and therefore a multidrop '
                'retriever'
            )

        # At least some security...
        if (os.geteuid() == 0 and not self.conf['allow_root_commands']
                and self.conf['user'] == None):
            raise getmailConfigurationError(
                'refuse to invoke external commands as root by default'
            )

        stdout = tempfile.TemporaryFile()
        stderr = tempfile.TemporaryFile()
        childpid = os.fork()

        if not childpid:
            # Child
            self._filter_command(msg, stdout, stderr)
        self.log.debug('spawned child %d\n' % childpid)

        # Parent
        exitcode = self._wait_for_child(childpid)

        stderr.seek(0)
        err = stderr.read().strip()

        self.log.debug('command %s %d exited %d\n' % (self.conf['command'],
                                                      childpid, exitcode))

        return (exitcode, msg, err)
