'''Extends :mod:`unittest` functionality by augmenting the way failed
assertions are handled to provide more actionable failure information
to the test consumer.

Briefly, by inheriting from :class:`marbles.AnnotatedTestCase` rather
than :class:`unittest.TestCase`, the test author gains the ability to
provide richer failure messages in their assert statements. These
messages can be format strings which are expanded using local
variables defined within the test itself.  The inclusion of this
additional information is enforced within the class.
'''

import ast
import collections.abc
import functools
import linecache
import sys
import textwrap
import traceback
import unittest


class AnnotationError(Exception):
    '''Raised when there is a problem with the way an assertion was
    annotated.
    '''
    pass


class AnnotatedAssertionError(AssertionError):
    '''AnnotatedAssertionError is an :class:`AssertionError` that
    expects a dictionary or tuple of additionional information beyond
    the static message string accepted by :class:`AssertionError`.

    The additional information provided is formatted with the context
    of the locals where the assertion error is raised. Annotated
    assertions expect an 'advice' key describing what to do if/when
    the assertion fails.

    See :class:`marbles.AnnotatedTestCase` for example usage. To make
    annotated assertions in your tests, your tests should inherit from
    :class:`marbles.AnnotatedTestCase` instead of
    :class:`unittest.TestCase`.
    '''

    _META_FORMAT_STRING = '''{standardMsg}
{message}

Source:
{assert_expr}
Locals:
\t{locals}
Advice:
\t{advice}
    '''
    REQUIRED_KEYS = ('message', 'advice')
    # If message and/or advice are declared in the test's scope and
    # passed as variables to the assert statement, instead of being
    # declared directly in the assert statement, we don't want to
    # display them in the Locals section of the test output because
    # both the message and the advice will be displayed elsewhere in
    # the output anyway
    _IGNORE_LOCALS = ['message', 'advice', 'self']

    def __init__(self, *args):
        '''Assume args contains a tuple of two arguments:
            1. the annotation provided by the test author, and
            2. the "standardMsg" from :mod:`unittest` which is the
               string representation of the asserted fact that wasn't
               true

        Annotation can be provided as either a tuple containing at
        least two strings, the first containing a message and the
        second containing advice, or as a dictionary containing the
        keys 'message' and 'advice'.  The 'message' and 'advice'
        arguments can also be passed as keyword arguments.

        ``message``
            This should be similar to the normal message provided to
            assert methods on :class:`TestCase`, but can contain
            format string directives using the names of local
            variables defined within the test itself.

        ``advice``
            Similarly to ``message``, this is formatted with local
            variables defined within the test itself, but this string
            is meant to inform the user of what to do when the test
            fails.
        '''
        # These attributes are publicly exposed as properties below to
        # facilitate programmatic interactions with test failures
        # (e.g., aggregating and formatting output into a consolidated
        # report)
        self._annotation, self._msg = args[0]
        self._locals = None
        self._filename = None
        self._linenumber = None
        self._assert_expr = None
        self._formatted_msg = None

        self._format_annotation()

        super(AnnotatedAssertionError, self).__init__(self.formattedMsg)

    @property
    def annotation(self):
        return self._annotation

    @property
    def standardMsg(self):  # use unittest's name
        return self._msg

    @property
    def locals(self):
        if not self._locals:
            self._get_stack()
        return self._locals

    @property
    def filename(self):
        if not self._filename:
            self._get_stack()
        return self._filename

    @property
    def linenumber(self):
        if not self._linenumber:
            self._get_stack()
        return self._linenumber

    @property
    def assert_expr(self):
        if not self._assert_expr:
            assert_expr = self._get_source(self.filename, self.linenumber)
            setattr(self, '_assert_expr', assert_expr)
        return self._assert_expr

    @property
    def formattedMsg(self):  # mimic unittest's name for standardMsg
        if not self._formatted_msg:
            formatted_msg = self._format_msg()
            setattr(self, '_formatted_msg', formatted_msg)
        return self._formatted_msg

    def _get_stack(self):
        '''Capture locals, filename, and line number from the stacktrace
        to provide the source of the assertion error and formatted
        advice.
        '''
        tb = traceback.walk_stack(sys._getframe().f_back)
        stack = traceback.StackSummary.extract(tb, capture_locals=True)

        for level in stack:
            # We want locals from the test definition (which always
            # begins with 'test_' in unittest), which will be at a
            # different level in the stack depending on how many tests
            # are in each test case, how many test cases there are,
            # etc.
            if level.name.startswith('test_'):
                setattr(self, '_locals', level.locals.copy())
                setattr(self, '_filename', level.filename)
                setattr(self, '_linenumber', level.lineno)
                break

    def _format_annotation(self):
        '''Format locals into into message and advice.'''
        formatted_message = self._annotation['message'].format(**self.locals)
        formatted_advice = self._annotation['advice'].format(**self.locals)

        # Break long strings onto multple lines for prettier output
        formatted_message = textwrap.wrap(formatted_message, 74)
        formatted_advice = textwrap.wrap(formatted_advice, 64)

        self._annotation['message'] = '\n'.join(formatted_message)
        self._annotation['advice'] = '\n\t'.join(formatted_advice)

    def _format_locals(self):
        locals_ = {k: v for k, v in self.locals.items()
                   if k not in self._IGNORE_LOCALS and not k.startswith('_')}

        return '\n\t'.join('{0}={1}'.format(k, v) for k, v in locals_.items())

    def _format_msg(self):
        return self._META_FORMAT_STRING.format(
            standardMsg=self.standardMsg, message=self.annotation['message'],
            assert_expr=self.assert_expr, advice=self.annotation['advice'],
            locals=self._format_locals())

    @staticmethod
    def _get_source(filename, linenumber, leading=1, following=2):
        '''Given a Python filename and line number, return all lines
        that are a part of the statement containing that line,
        indicating the line number given with '>'.

        Python stacktraces, when reporting which line they're on,
        always show the last line of the statement. This can be
        confusing if the statement spans multiple lines. This function
        will reconstruct the whole statement.
        '''
        _source = ''.join(linecache.getlines(filename))
        _tree = ast.parse(_source)

        for node in ast.walk(_tree):
            if isinstance(node, ast.stmt):
                statement = node
            if hasattr(node, 'lineno') and node.lineno == linenumber:
                line_range = list(
                    range(statement.lineno - leading, linenumber + following))
                source = [linecache.getline(filename, x) for x in line_range]
                break  # we have the lines we're after and can stop walking

        # Dedent the source, removing the final newline added by dedent
        dedented_lines = textwrap.dedent(''.join(source)).split('\n')[:-1]

        formatted_lines = []
        for i, line in zip(line_range, dedented_lines):
            prefix = '>' if i == statement.lineno else ' '
            formatted_lines.append(' {0} {1:4d} {2}'.format(prefix, i, line))

        return '\n'.join(formatted_lines)


class AnnotatedTestCase(unittest.TestCase):
    '''AnnotatedTestCase is an extension of :class:`unittest.TestCase`.

    When writing a test class based on :class:`AnnotatedTestCase`, all
    assert statements like :meth:`unittest.TestCase.assertEqual`,
    rather than accepting an optional final string parameter ``msg``,
    require a dictionary or tuple containing two stings: a message and
    some advice.  Alternatively, ``message`` and ``advice`` can be
    passed as keyword arguments to the assertion function.

    The message and advice strings provided are formatted with
    :meth:`str.format` given the local variables defined within the
    test itself.

    Example:

    .. code-block:: py

        import os
        import re

        from marbles import AnnotatedTestCase

        class ExampleTestCase(AnnotatedTestCase):

            def test_filename_pattern(self):
                expected = '^file_[0-9]{8}$'
                actual = os.path.splittext('file_2016_01_01.py')[0]

                message = 'Filename {actual} does not match the pattern {expected}.'
                advice = ('Determine if this is a one-off error or if the file naming '
                          'pattern has changed. If the file naming pattern has changed, '
                          'consider updating this test.')
                self.assertIsNotNone(re.search(expected, actual), (message, advice))
    '''  # noqa: E501

    failureException = AnnotatedAssertionError

    def _formatMessage(self, msg, standardMsg):
        return (msg, standardMsg)

    @staticmethod
    def _coerce_annotation_dict(annotation):
        '''Transform the annotation into a dict.

        If a tuple was provided, coerce it to a dictionary for ease of
        use elsewhere in AnnotatedAssertionError.
        '''
        if isinstance(annotation, collections.abc.Sequence):
            return dict(zip(AnnotatedAssertionError.REQUIRED_KEYS, annotation))
        elif isinstance(annotation, collections.abc.Mapping):
            return annotation
        else:
            error = 'Annotation type not supported: {0}'.format(
                type(annotation))
            raise AnnotationError(error)

    @staticmethod
    def _validate_annotation(annotation):
        '''Ensures that the annotation has the right fields.'''
        required = set(AnnotatedAssertionError.REQUIRED_KEYS)
        missing = required.difference(set(annotation.keys()))
        if missing:
            error = 'Annotation missing required fields: {0}'.format(missing)
            raise AnnotationError(error)

    def __getattribute__(self, key):
        '''Keyword argument support for assertions.

        We want AnnotatedTestCases to be able to call assertions with
        syntax like this:

            self.assertTrue(True, message='message', advice='advice')

        in addition to this:

            self.assertTrue(True, ('message', 'advice'))

        To do so, we override __getattribute__ so that any method that
        gets looked up and starts with 'assert' gets wrapped so that
        it does what we want.  We override __getattribute__ rather
        than __getattr__ because __getattr__ doesn't get called when
        the method just exists.

        To add other keyword arguments in the future, you have to make
        sure that the way the underlying assertion gets called is
        going to work with _formatMessage above, and the unpacking of
        args in AnnotatedAssertionError.__init__, and you should watch
        out for backwards compatibility with existing usage.
        '''
        attr = object.__getattribute__(self, key)

        if callable(attr) and key.startswith('assert'):
            @functools.wraps(attr)
            def wrapper(*args, **kwargs):
                required_keys = set(AnnotatedAssertionError.REQUIRED_KEYS)

                provided_keys = set(kwargs).intersection(required_keys)
                if provided_keys:
                    bundle = {key: kwargs.pop(key) for key in provided_keys}
                    annotation = self._coerce_annotation_dict(bundle)
                else:
                    if 'msg' in kwargs:
                        msg = kwargs.pop('msg')
                    else:
                        msg = args[-1]
                        args = args[:-1]
                    annotation = self._coerce_annotation_dict(msg)

                self._validate_annotation(annotation)
                return attr(*args, msg=annotation, **kwargs)
            return wrapper

        return attr