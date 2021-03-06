import py, sys
from pypy.interpreter.astcompiler import codegen, astbuilder, symtable, optimize
from pypy.interpreter.pyparser import pyparse
from pypy.interpreter.pyparser.test import expressions
from pypy.interpreter.pycode import PyCode
from pypy.interpreter.pyparser.error import SyntaxError, IndentationError
from pypy.tool import stdlib_opcode as ops

def compile_with_astcompiler(expr, mode, space):
    p = pyparse.PythonParser(space)
    info = pyparse.CompileInfo("<test>", mode)
    cst = p.parse_source(expr, info)
    ast = astbuilder.ast_from_node(space, cst, info)
    return codegen.compile_ast(space, ast, info)

def generate_function_code(expr, space):
    p = pyparse.PythonParser(space)
    info = pyparse.CompileInfo("<test>", 'exec')
    cst = p.parse_source(expr, info)
    ast = astbuilder.ast_from_node(space, cst, info)
    function_ast = optimize.optimize_ast(space, ast.body[0], info)
    function_ast = ast.body[0]
    symbols = symtable.SymtableBuilder(space, ast, info)
    generator = codegen.FunctionCodeGenerator(
        space, 'function', function_ast, 1, symbols, info)
    blocks = generator.first_block.post_order()
    generator._resolve_block_targets(blocks)
    return generator, blocks

class BaseTestCompiler:
    """These tests compile snippets of code and check them by
    running them with our own interpreter.  These are thus not
    completely *unit* tests, but given that our interpreter is
    pretty stable now it is the best way I could find to check
    the compiler.
    """

    def run(self, source):
        import sys
        source = str(py.code.Source(source))
        space = self.space
        code = compile_with_astcompiler(source, 'exec', space)
        # 2.7 bytecode is too different, the standard `dis` module crashes
        # on older cpython versions
        if sys.version_info >= (2, 7):
            print
            code.dump()
        w_dict = space.newdict()
        code.exec_code(space, w_dict, w_dict)
        return w_dict

    def check(self, w_dict, evalexpr, expected):
        # for now, we compile evalexpr with CPython's compiler but run
        # it with our own interpreter to extract the data from w_dict
        co_expr = compile(evalexpr, '<evalexpr>', 'eval')
        space = self.space
        pyco_expr = PyCode._from_code(space, co_expr)
        w_res = pyco_expr.exec_host_bytecode(w_dict, w_dict)
        res = space.str_w(space.repr(w_res))
        if not isinstance(expected, float):
            noL = lambda expr: expr.replace('L', '')
            assert noL(res) == noL(repr(expected))
        else:
            # Float representation can vary a bit between interpreter
            # versions, compare the numbers instead.
            assert eval(res) == expected

    def simple_test(self, source, evalexpr, expected):
        w_g = self.run(source)
        self.check(w_g, evalexpr, expected)

    st = simple_test

    def error_test(self, source, exc_type):
        py.test.raises(exc_type, self.simple_test, source, None, None)


class TestCompiler(BaseTestCompiler):

    def test_issue_713(self):
        func = "def f(_=2): return (_ if _ else _) if False else _"
        yield self.st, func, "f()", 2

    def test_long_jump(self):
        func = """def f(x):
    y = 0
    if x:
%s        return 1
    else:
        return 0""" % ("        y += 1\n" * 6700,)
        yield self.st, func, "f(1)", 1
        yield self.st, func, "f(0)", 0

    def test_argtuple(self):
        yield (self.simple_test, "def f( x, (y,z) ): return x,y,z",
               "f((1,2),(3,4))", ((1,2),3,4))
        yield (self.simple_test, "def f( x, (y,(z,t)) ): return x,y,z,t",
               "f(1,(2,(3,4)))", (1,2,3,4))
        yield (self.simple_test, "def f(((((x,),y),z),t),u): return x,y,z,t,u",
               "f(((((1,),2),3),4),5)", (1,2,3,4,5))

    def test_constants(self):
        for c in expressions.constants:
            yield (self.simple_test, "x="+c, "x", eval(c))

    def test_neg_sys_maxint(self):
        import sys
        stmt = "x = %s" % (-sys.maxint-1)
        self.simple_test(stmt, "type(x)", int)

    def test_tuple_assign(self):
        yield self.error_test, "() = 1", SyntaxError
        yield self.simple_test, "x,= 1,", "x", 1
        yield self.simple_test, "x,y = 1,2", "x,y", (1, 2)
        yield self.simple_test, "x,y,z = 1,2,3", "x,y,z", (1, 2, 3)
        yield self.simple_test, "x,y,z,t = 1,2,3,4", "x,y,z,t", (1, 2, 3, 4)
        yield self.simple_test, "x,y,x,t = 1,2,3,4", "x,y,t", (3, 2, 4)
        yield self.simple_test, "[] = []", "1", 1
        yield self.simple_test, "[x]= 1,", "x", 1
        yield self.simple_test, "[x,y] = [1,2]", "x,y", (1, 2)
        yield self.simple_test, "[x,y,z] = 1,2,3", "x,y,z", (1, 2, 3)
        yield self.simple_test, "[x,y,z,t] = [1,2,3,4]", "x,y,z,t", (1, 2, 3,4)
        yield self.simple_test, "[x,y,x,t] = 1,2,3,4", "x,y,t", (3, 2, 4)

    def test_tuple_assign_order(self):
        decl = py.code.Source("""
            class A:
                def __getattr__(self, name):
                    global seen
                    seen += name
                    return name
                def __setattr__(self, name, value):
                    global seen
                    seen += '%s=%s' % (name, value)
            seen = ''
            a = A()
        """)
        decl = str(decl) + '\n'
        yield self.st, decl+"a.x,= a.a,", 'seen', 'ax=a'
        yield self.st, decl+"a.x,a.y = a.a,a.b", 'seen', 'abx=ay=b'
        yield self.st, decl+"a.x,a.y,a.z = a.a,a.b,a.c", 'seen', 'abcx=ay=bz=c'
        yield self.st, decl+"a.x,a.y,a.x,a.t = a.a,a.b,a.c,a.d", 'seen', \
            'abcdx=ay=bx=ct=d'
        yield self.st, decl+"[a.x] = [a.a]", 'seen', 'ax=a'
        yield self.st, decl+"[a.x,a.y] = a.a,a.b", 'seen', 'abx=ay=b'
        yield self.st, decl+"[a.x,a.y,a.z] = [a.a,a.b,a.c]", 'seen', \
            'abcx=ay=bz=c'
        yield self.st, decl+"[a.x,a.y,a.x,a.t] = a.a,a.b,a.c,a.d", 'seen', \
            'abcdx=ay=bx=ct=d'

    def test_binary_operator(self):
        for operator in ['+', '-', '*', '**', '/', '&', '|', '^', '//',
                         '<<', '>>', 'and', 'or', '<', '>', '<=', '>=',
                         'is', 'is not']:
            expected = eval("17 %s 5" % operator)
            yield self.simple_test, "x = 17 %s 5" % operator, "x", expected
            expected = eval("0 %s 11" % operator)
            yield self.simple_test, "x = 0 %s 11" % operator, "x", expected

    def test_compare(self):
        yield self.st, "x = 2; y = 5; y; h = 1 < x >= 3 < x", "h", False

    def test_augmented_assignment(self):
        for operator in ['+', '-', '*', '**', '/', '&', '|', '^', '//',
                         '<<', '>>']:
            expected = eval("17 %s 5" % operator)
            yield self.simple_test, "x = 17; x %s= 5" % operator, "x", expected

    def test_subscript(self):
        yield self.simple_test, "d={2:3}; x=d[2]", "x", 3
        yield self.simple_test, "d={(2,):3}; x=d[2,]", "x", 3
        yield self.simple_test, "d={}; d[1]=len(d); x=d[len(d)]", "x", 0
        yield self.simple_test, "d={}; d[1]=3; del d[1]", "len(d)", 0

    def test_attribute(self):
        yield self.simple_test, """
            class A:
                pass
            a1 = A()
            a2 = A()
            a1.bc = A()
            a1.bc.de = a2
            a2.see = 4
            a1.bc.de.see += 3
            x = a1.bc.de.see
        """, 'x', 7

    def test_slice(self):
        decl = py.code.Source("""
            class A(object):
                def __getitem__(self, x):
                    global got
                    got = x
                def __setitem__(self, x, y):
                    global set
                    set = x
                def __delitem__(self, x):
                    global deleted
                    deleted = x
            a = A()
        """)
        decl = str(decl) + '\n'
        testcases = ['[:]',    '[:,9]',    '[8,:]',
                     '[2:]',   '[2:,9]',   '[8,2:]',
                     '[:2]',   '[:2,9]',   '[8,:2]',
                     '[4:7]',  '[4:7,9]',  '[8,4:7]',
                     '[::]',   '[::,9]',   '[8,::]',
                     '[2::]',  '[2::,9]',  '[8,2::]',
                     '[:2:]',  '[:2:,9]',  '[8,:2:]',
                     '[4:7:]', '[4:7:,9]', '[8,4:7:]',
                     '[::3]',  '[::3,9]',  '[8,::3]',
                     '[2::3]', '[2::3,9]', '[8,2::3]',
                     '[:2:3]', '[:2:3,9]', '[8,:2:3]',
                     '[4:7:3]','[4:7:3,9]','[8,4:7:3]',
                     ]
        class Checker(object):
            def __getitem__(self, x):
                self.got = x
        checker = Checker()
        for testcase in testcases:
            exec "checker" + testcase
            yield self.st, decl + "a" + testcase, "got", checker.got
            yield self.st, decl + "a" + testcase + ' = 5', "set", checker.got
            yield self.st, decl + "del a" + testcase, "deleted", checker.got

    def test_funccalls(self):
        decl = py.code.Source("""
            def f(*args, **kwds):
                kwds = kwds.items()
                kwds.sort()
                return list(args) + kwds
        """)
        decl = str(decl) + '\n'
        yield self.st, decl + "x=f()", "x", []
        yield self.st, decl + "x=f(5)", "x", [5]
        yield self.st, decl + "x=f(5, 6, 7, 8)", "x", [5, 6, 7, 8]
        yield self.st, decl + "x=f(a=2, b=5)", "x", [('a',2), ('b',5)]
        yield self.st, decl + "x=f(5, b=2, *[6,7])", "x", [5, 6, 7, ('b', 2)]
        yield self.st, decl + "x=f(5, b=2, **{'a': 8})", "x", [5, ('a', 8),
                                                                  ('b', 2)]

    def test_listmakers(self):
        yield (self.st,
               "l = [(j, i) for j in range(10) for i in range(j)"
               + " if (i+j)%2 == 0 and i%3 == 0]",
               "l",
               [(2, 0), (4, 0), (5, 3), (6, 0),
                (7, 3), (8, 0), (8, 6), (9, 3)])

    def test_genexprs(self):
        yield (self.st,
               "l = list((j, i) for j in range(10) for i in range(j)"
               + " if (i+j)%2 == 0 and i%3 == 0)",
               "l",
               [(2, 0), (4, 0), (5, 3), (6, 0),
                (7, 3), (8, 0), (8, 6), (9, 3)])

    def test_comparisons(self):
        yield self.st, "x = 3 in {3: 5}", "x", True
        yield self.st, "x = 3 not in {3: 5}", "x", False
        yield self.st, "t = True; x = t is True", "x", True
        yield self.st, "t = True; x = t is False", "x", False
        yield self.st, "t = True; x = t is None", "x", False
        yield self.st, "n = None; x = n is True", "x", False
        yield self.st, "n = None; x = n is False", "x", False
        yield self.st, "n = None; x = n is None", "x", True
        yield self.st, "t = True; x = t is not True", "x", False
        yield self.st, "t = True; x = t is not False", "x", True
        yield self.st, "t = True; x = t is not None", "x", True
        yield self.st, "n = None; x = n is not True", "x", True
        yield self.st, "n = None; x = n is not False", "x", True
        yield self.st, "n = None; x = n is not None", "x", False

        yield self.st, "x = not (3 in {3: 5})", "x", False
        yield self.st, "x = not (3 not in {3: 5})", "x", True
        yield self.st, "t = True; x = not (t is True)", "x", False
        yield self.st, "t = True; x = not (t is False)", "x", True
        yield self.st, "t = True; x = not (t is None)", "x", True
        yield self.st, "n = None; x = not (n is True)", "x", True
        yield self.st, "n = None; x = not (n is False)", "x", True
        yield self.st, "n = None; x = not (n is None)", "x", False
        yield self.st, "t = True; x = not (t is not True)", "x", True
        yield self.st, "t = True; x = not (t is not False)", "x", False
        yield self.st, "t = True; x = not (t is not None)", "x", False
        yield self.st, "n = None; x = not (n is not True)", "x", False
        yield self.st, "n = None; x = not (n is not False)", "x", False
        yield self.st, "n = None; x = not (n is not None)", "x", True

    def test_multiexpr(self):
        yield self.st, "z = 2+3; x = y = z", "x,y,z", (5,5,5)

    def test_imports(self):
        import os
        yield self.st, "import sys", "sys.__name__", "sys"
        yield self.st, "import sys as y", "y.__name__", "sys"
        yield (self.st, "import sys, os",
               "sys.__name__, os.__name__", ("sys", "os"))
        yield (self.st, "import sys as x, os.path as y",
               "x.__name__, y.__name__", ("sys", os.path.__name__))
        yield self.st, 'import os.path', "os.path.__name__", os.path.__name__
        yield (self.st, 'import os.path, sys',
               "os.path.__name__, sys.__name__", (os.path.__name__, "sys"))
        yield (self.st, 'import sys, os.path as osp',
               "osp.__name__, sys.__name__", (os.path.__name__, "sys"))
        yield (self.st, 'import os.path as osp',
               "osp.__name__", os.path.__name__)
        yield (self.st, 'from os import path',
               "path.__name__", os.path.__name__)
        yield (self.st, 'from os import path, sep',
               "path.__name__, sep", (os.path.__name__, os.sep))
        yield (self.st, 'from os import path as p',
               "p.__name__", os.path.__name__)
        yield (self.st, 'from os import *',
               "path.__name__, sep", (os.path.__name__, os.sep))
        yield (self.st, '''
            class A(object):
                def m(self):
                    from __foo__.bar import x
            try:
                A().m()
            except ImportError, e:
                msg = str(e)
            ''', "msg", "No module named __foo__")

    def test_if_stmts(self):
        yield self.st, "a = 42\nif a > 10: a += 2", "a", 44
        yield self.st, "a=5\nif 0: a=7", "a", 5
        yield self.st, "a=5\nif 1: a=7", "a", 7
        yield self.st, "a=5\nif a and not not (a<10): a=7", "a", 7
        yield self.st, """
            lst = []
            for a in range(10):
                if a < 3:
                    a += 20
                elif a > 3 and a < 8:
                    a += 30
                else:
                    a += 40
                lst.append(a)
            """, "lst", [20, 21, 22, 43, 34, 35, 36, 37, 48, 49]
        yield self.st, """
            lst = []
            for a in range(10):
                b = (a & 7) ^ 1
                if a or 1 or b: lst.append('A')
                if a or 0 or b: lst.append('B')
                if a and 1 and b: lst.append('C')
                if a and 0 and b: lst.append('D')
                if not (a or 1 or b): lst.append('-A')
                if not (a or 0 or b): lst.append('-B')
                if not (a and 1 and b): lst.append('-C')
                if not (a and 0 and b): lst.append('-D')
                if (not a) or (not 1) or (not b): lst.append('A')
                if (not a) or (not 0) or (not b): lst.append('B')
                if (not a) and (not 1) and (not b): lst.append('C')
                if (not a) and (not 0) and (not b): lst.append('D')
            """, "lst", ['A', 'B', '-C', '-D', 'A', 'B', 'A', 'B', '-C',
                         '-D', 'A', 'B', 'A', 'B', 'C', '-D', 'B', 'A', 'B',
                         'C', '-D', 'B', 'A', 'B', 'C', '-D', 'B', 'A', 'B',
                         'C', '-D', 'B', 'A', 'B', 'C', '-D', 'B', 'A', 'B',
                         'C', '-D', 'B', 'A', 'B', 'C', '-D', 'B', 'A', 'B',
                         '-C', '-D', 'A', 'B']

    def test_docstrings(self):
        for source, expected in [
            ('''def foo(): return 1''',      None),
            ('''class foo: pass''',          None),
            ('''foo = lambda: 4''',          None),
            ('''foo = lambda: "foo"''',      None),
            ('''def foo(): 4''',             None),
            ('''class foo: "foo"''',         "foo"),
            ('''def foo():
                    """foo docstring"""
                    return 1
             ''',                            "foo docstring"),
            ('''def foo():
                    """foo docstring"""
                    a = 1
                    """bar"""
                    return a
             ''',                            "foo docstring"),
            ('''def foo():
                    """doc"""; print 1
                    a=1
             ''',                            "doc"),
            ('''
                class Foo(object): pass
                foo = Foo()
                exec "'moduledoc'" in foo.__dict__
             ''',                            "moduledoc"),
            ]:
            yield self.simple_test, source, "foo.__doc__", expected

    def test_in(self):
        yield self.st, "n = 5; x = n in [3,4,5]", 'x', True
        yield self.st, "n = 5; x = n in [3,4,6]", 'x', False
        yield self.st, "n = 5; x = n in [3,4,n]", 'x', True
        yield self.st, "n = 5; x = n in [3,4,n+1]", 'x', False
        yield self.st, "n = 5; x = n in (3,4,5)", 'x', True
        yield self.st, "n = 5; x = n in (3,4,6)", 'x', False
        yield self.st, "n = 5; x = n in (3,4,n)", 'x', True
        yield self.st, "n = 5; x = n in (3,4,n+1)", 'x', False

    def test_for_loops(self):
        yield self.st, """
            total = 0
            for i in [2, 7, 5]:
                total += i
        """, 'total', 2 + 7 + 5
        yield self.st, """
            total = 0
            for i in (2, 7, 5):
                total += i
        """, 'total', 2 + 7 + 5
        yield self.st, """
            total = 0
            for i in [2, 7, total+5]:
                total += i
        """, 'total', 2 + 7 + 5
        yield self.st, "x = sum([n+2 for n in [6, 1, 2]])", 'x', 15
        yield self.st, "x = sum([n+2 for n in (6, 1, 2)])", 'x', 15
        yield self.st, "k=2; x = sum([n+2 for n in [6, 1, k]])", 'x', 15
        yield self.st, "k=2; x = sum([n+2 for n in (6, 1, k)])", 'x', 15
        yield self.st, "x = sum(n+2 for n in [6, 1, 2])", 'x', 15
        yield self.st, "x = sum(n+2 for n in (6, 1, 2))", 'x', 15
        yield self.st, "k=2; x = sum(n+2 for n in [6, 1, k])", 'x', 15
        yield self.st, "k=2; x = sum(n+2 for n in (6, 1, k))", 'x', 15

    def test_closure(self):
        decl = py.code.Source("""
            def make_adder(n):
                def add(m):
                    return n + m
                return add
        """)
        decl = str(decl) + "\n"
        yield self.st, decl + "x = make_adder(40)(2)", 'x', 42

        decl = py.code.Source("""
            def f(a, g, e, c):
                def b(n, d):
                    return (a, c, d, g, n)
                def f(b, a):
                    return (a, b, c, g)
                return (a, g, e, c, b, f)
            A, G, E, C, B, F = f(6, 2, 8, 5)
            A1, C1, D1, G1, N1 = B(7, 3)
            A2, B2, C2, G2 = F(1, 4)
        """)
        decl = str(decl) + "\n"
        yield self.st, decl, 'A,A1,A2,B2,C,C1,C2,D1,E,G,G1,G2,N1', \
                             (6,6 ,4 ,1 ,5,5 ,5 ,3 ,8,2,2 ,2 ,7 )

        decl = py.code.Source("""
            def f((a, b)):
                def g((c, d)):
                    return (a, b, c, d)
                return g
            x = f((1, 2))((3, 4))
        """)
        decl = str(decl) + "\n"
        yield self.st, decl, 'x', (1, 2, 3, 4)

    def test_closure_error(self):
        source = """if 1:
        def f(a):
            del a
            def x():
                a
        """
        with py.test.raises(SyntaxError) as excinfo:
            self.run(source)
        msg = excinfo.value.msg
        assert msg == "Can't delete variable used in nested scopes: 'a'"

    def test_try_except_finally(self):
        yield self.simple_test, """
            try:
                x = 5
                try:
                    if x > 2:
                        raise ValueError
                finally:
                    x += 1
            except ValueError:
                x *= 7
        """, 'x', 42

    def test_while_loop(self):
        yield self.simple_test, """
            comments = [42]
            comment = '# foo'
            while comment[:1] == '#':
                comments[:0] = [comment]
                comment = ''
        """, 'comments', ['# foo', 42]
        yield self.simple_test, """
             while 0:
                 pass
             else:
                 x = 1
        """, "x", 1

    def test_return_lineno(self):
        # the point of this test is to check that there is no code associated
        # with any line greater than 4.
        # The implict return will have the line number of the last statement
        # so we check that that line contains exactly the implicit return None
        yield self.simple_test, """\
            def ireturn_example():    # line 1
                global b              # line 2
                if a == b:            # line 3
                    b = a+1           # line 4
                else:                 # line 5
                    if 1: pass        # line 6
            import dis
            co = ireturn_example.func_code
            linestarts = list(dis.findlinestarts(co))
            addrreturn = linestarts[-1][0]
            x = [addrreturn == (len(co.co_code) - 4)]
            x.extend([lineno for addr, lineno in linestarts])
        """, 'x', [True, 3, 4, 6]

    def test_type_of_constants(self):
        yield self.simple_test, "x=[0, 0L]", 'type(x[1])', long
        yield self.simple_test, "x=[(1,0), (1,0L)]", 'type(x[1][1])', long
        yield self.simple_test, "x=['2?-', '2?-']", 'id(x[0])==id(x[1])', True

    def test_pprint(self):
        # a larger example that showed a bug with jumps
        # over more than 256 bytes
        decl = py.code.Source("""
            def _safe_repr(object, context, maxlevels, level):
                typ = type(object)
                if typ is str:
                    if 'locale' not in _sys.modules:
                        return repr(object), True, False
                    if "'" in object and '"' not in object:
                        closure = '"'
                        quotes = {'"': '\\"'}
                    else:
                        closure = "'"
                        quotes = {"'": "\\'"}
                    qget = quotes.get
                    sio = _StringIO()
                    write = sio.write
                    for char in object:
                        if char.isalpha():
                            write(char)
                        else:
                            write(qget(char, repr(char)[1:-1]))
                    return ("%s%s%s" % (closure, sio.getvalue(), closure)), True, False

                r = getattr(typ, "__repr__", None)
                if issubclass(typ, dict) and r is dict.__repr__:
                    if not object:
                        return "{}", True, False
                    objid = id(object)
                    if maxlevels and level > maxlevels:
                        return "{...}", False, objid in context
                    if objid in context:
                        return _recursion(object), False, True
                    context[objid] = 1
                    readable = True
                    recursive = False
                    components = []
                    append = components.append
                    level += 1
                    saferepr = _safe_repr
                    for k, v in object.iteritems():
                        krepr, kreadable, krecur = saferepr(k, context, maxlevels, level)
                        vrepr, vreadable, vrecur = saferepr(v, context, maxlevels, level)
                        append("%s: %s" % (krepr, vrepr))
                        readable = readable and kreadable and vreadable
                        if krecur or vrecur:
                            recursive = True
                    del context[objid]
                    return "{%s}" % ', '.join(components), readable, recursive

                if (issubclass(typ, list) and r is list.__repr__) or \
                   (issubclass(typ, tuple) and r is tuple.__repr__):
                    if issubclass(typ, list):
                        if not object:
                            return "[]", True, False
                        format = "[%s]"
                    elif _len(object) == 1:
                        format = "(%s,)"
                    else:
                        if not object:
                            return "()", True, False
                        format = "(%s)"
                    objid = id(object)
                    if maxlevels and level > maxlevels:
                        return format % "...", False, objid in context
                    if objid in context:
                        return _recursion(object), False, True
                    context[objid] = 1
                    readable = True
                    recursive = False
                    components = []
                    append = components.append
                    level += 1
                    for o in object:
                        orepr, oreadable, orecur = _safe_repr(o, context, maxlevels, level)
                        append(orepr)
                        if not oreadable:
                            readable = False
                        if orecur:
                            recursive = True
                    del context[objid]
                    return format % ', '.join(components), readable, recursive

                rep = repr(object)
                return rep, (rep and not rep.startswith('<')), False
        """)
        decl = str(decl) + '\n'
        g = {}
        exec decl in g
        expected = g['_safe_repr']([5], {}, 3, 0)
        yield self.st, decl + 'x=_safe_repr([5], {}, 3, 0)', 'x', expected

    def test_mapping_test(self):
        decl = py.code.Source("""
            class X(object):
                reference = {1:2, "key1":"value1", "key2":(1,2,3)}
                key, value = reference.popitem()
                other = {key:value}
                key, value = reference.popitem()
                inmapping = {key:value}
                reference[key] = value
                def _empty_mapping(self):
                    return {}
                _full_mapping = dict
                def assertEqual(self, x, y):
                    assert x == y
                failUnlessRaises = staticmethod(raises)
                def assert_(self, x):
                    assert x
                def failIf(self, x):
                    assert not x

            def test_read(self):
                # Test for read only operations on mapping
                p = self._empty_mapping()
                p1 = dict(p) #workaround for singleton objects
                d = self._full_mapping(self.reference)
                if d is p:
                    p = p1
                #Indexing
                for key, value in self.reference.items():
                    self.assertEqual(d[key], value)
                knownkey = self.other.keys()[0]
                self.failUnlessRaises(KeyError, lambda:d[knownkey])
                #len
                self.assertEqual(len(p), 0)
                self.assertEqual(len(d), len(self.reference))
                #has_key
                for k in self.reference:
                    self.assert_(d.has_key(k))
                    self.assert_(k in d)
                for k in self.other:
                    self.failIf(d.has_key(k))
                    self.failIf(k in d)
                #cmp
                self.assertEqual(cmp(p,p), 0)
                self.assertEqual(cmp(d,d), 0)
                self.assertEqual(cmp(p,d), -1)
                self.assertEqual(cmp(d,p), 1)
                #__non__zero__
                if p: self.fail("Empty mapping must compare to False")
                if not d: self.fail("Full mapping must compare to True")
                # keys(), items(), iterkeys() ...
                def check_iterandlist(iter, lst, ref):
                    self.assert_(hasattr(iter, 'next'))
                    self.assert_(hasattr(iter, '__iter__'))
                    x = list(iter)
                    self.assert_(set(x)==set(lst)==set(ref))
                check_iterandlist(d.iterkeys(), d.keys(), self.reference.keys())
                check_iterandlist(iter(d), d.keys(), self.reference.keys())
                check_iterandlist(d.itervalues(), d.values(), self.reference.values())
                check_iterandlist(d.iteritems(), d.items(), self.reference.items())
                #get
                key, value = d.iteritems().next()
                knownkey, knownvalue = self.other.iteritems().next()
                self.assertEqual(d.get(key, knownvalue), value)
                self.assertEqual(d.get(knownkey, knownvalue), knownvalue)
                self.failIf(knownkey in d)
                return 42
        """)
        decl = str(decl) + '\n'
        yield self.simple_test, decl + 'r = test_read(X())', 'r', 42

    def test_stack_depth_bug(self):
        decl = py.code.Source("""
        class A:
            def initialize(self):
                # install all the MultiMethods into the space instance
                if isinstance(mm, object):
                    def make_boundmethod(func=func):
                        def boundmethod(*args):
                            return func(self, *args)
        r = None
        """)
        decl = str(decl) + '\n'
        yield self.simple_test, decl, 'r', None

    def test_indentation_error(self):
        source = py.code.Source("""
        x
         y
        """)
        try:
            self.simple_test(source, None, None)
        except IndentationError as e:
            assert e.msg == 'unexpected indent'
        else:
            raise Exception("DID NOT RAISE")

    def test_no_indent(self):
        source = py.code.Source("""
        def f():
        xxx
        """)
        try:
            self.simple_test(source, None, None)
        except IndentationError as e:
            assert e.msg == 'expected an indented block'
        else:
            raise Exception("DID NOT RAISE")

    def test_indent_error_filename(self):
        source = py.code.Source("""
        def f():
          x
         y
        """)
        try:
            self.simple_test(source, None, None)
        except IndentationError as e:
            assert e.filename == '<test>'
        else:
            raise Exception("DID NOT RAISE")

    def test_kwargs_last(self):
        py.test.raises(SyntaxError, self.simple_test, "int(base=10, '2')",
                       None, None)

    def test_crap_after_starargs(self):
        source = "call(*args, *args)"
        py.test.raises(SyntaxError, self.simple_test, source, None, None)

    def test_not_a_name(self):
        source = "call(a, b, c, 3=3)"
        py.test.raises(SyntaxError, self.simple_test, source, None, None)

    def test_assignment_to_call_func(self):
        source = "call(a, b, c) = 3"
        py.test.raises(SyntaxError, self.simple_test, source, None, None)

    def test_augassig_to_sequence(self):
        source = "a, b += 3"
        py.test.raises(SyntaxError, self.simple_test, source, None, None)

    def test_broken_setups(self):
        source = """if 1:
        try:
           break
        finally:
           pass
        """
        py.test.raises(SyntaxError, self.simple_test, source, None, None)

    def test_unpack_singletuple(self):
        source = """if 1:
        l = []
        for x, in [(1,), (2,)]:
            l.append(x)
        """
        self.simple_test(source, 'l', [1, 2])

    def test_unpack_wrong_stackeffect(self):
        source = """if 1:
        l = [1, 2]
        a, b = l
        a, b = l
        a, b = l
        a, b = l
        a, b = l
        a, b = l
        """
        code = compile_with_astcompiler(source, 'exec', self.space)
        assert code.co_stacksize == 2

    def test_stackeffect_bug3(self):
        source = """if 1:
        try: pass
        finally: pass
        try: pass
        finally: pass
        try: pass
        finally: pass
        try: pass
        finally: pass
        try: pass
        finally: pass
        try: pass
        finally: pass
        """
        code = compile_with_astcompiler(source, 'exec', self.space)
        assert code.co_stacksize == 3

    def test_stackeffect_bug4(self):
        source = """if 1:
        with a: pass
        with a: pass
        with a: pass
        with a: pass
        with a: pass
        with a: pass
        """
        code = compile_with_astcompiler(source, 'exec', self.space)
        assert code.co_stacksize == 4

    def test_stackeffect_bug5(self):
        source = """if 1:
        a[:]; a[:]; a[:]; a[:]; a[:]; a[:]
        a[1:]; a[1:]; a[1:]; a[1:]; a[1:]; a[1:]
        a[:2]; a[:2]; a[:2]; a[:2]; a[:2]; a[:2]
        a[1:2]; a[1:2]; a[1:2]; a[1:2]; a[1:2]; a[1:2]
        """
        code = compile_with_astcompiler(source, 'exec', self.space)
        assert code.co_stacksize == 3

    def test_stackeffect_bug6(self):
        source = """if 1:
        {1}; {1}; {1}; {1}; {1}; {1}; {1}
        """
        code = compile_with_astcompiler(source, 'exec', self.space)
        assert code.co_stacksize == 1

    def test_stackeffect_bug7(self):
        source = '''def f():
            for i in a:
                return
        '''
        code = compile_with_astcompiler(source, 'exec', self.space)

    def test_lambda(self):
        yield self.st, "y = lambda x: x", "y(4)", 4

    def test_backquote_repr(self):
        yield self.st, "x = None; y = `x`", "y", "None"

    def test_deleting_attributes(self):
        test = """if 1:
        class X():
           x = 3
        del X.x
        try:
            X.x
        except AttributeError:
            pass
        else:
            raise AssertionError("attribute not removed")"""
        yield self.st, test, "X.__name__", "X"

    def test_lots_of_loops(self):
        source = "for x in y: pass\n" * 1000
        compile_with_astcompiler(source, 'exec', self.space)

    def test_assign_to_empty_list_1(self):
        source = """if 1:
        for i in range(5):
            del []
            [] = ()
            [] = []
            [] = [] = []
        ok = 1
        """
        self.simple_test(source, 'ok', 1)

    def test_assign_to_empty_list_2(self):
        source = """if 1:
        for i in range(5):
            try: [] = 1, 2, 3
            except ValueError: pass
            else: raise AssertionError
            try: [] = a = 1
            except TypeError: pass
            else: raise AssertionError
            try: [] = _ = iter(['foo'])
            except ValueError: pass
            else: raise AssertionError
            try: [], _ = iter(['foo']), 1
            except ValueError: pass
            else: raise AssertionError
        ok = 1
        """
        self.simple_test(source, 'ok', 1)

    @py.test.mark.parametrize('expr, result', [
        ("f1.__doc__", None),
        ("f2.__doc__", 'docstring'),
        ("f2()", 'docstring'),
        ("f3.__doc__", None),
        ("f3()", 'bar'),
        ("C1.__doc__", None),
        ("C2.__doc__", 'docstring'),
        ("C3.field", 'not docstring'),
        ("C4.field", 'docstring'),
        ("C4.__doc__", 'docstring'),
        ("C4.__doc__", 'docstring'),
        ("__doc__", None),])
    def test_remove_docstring(self, expr, result):
        source = '"module_docstring"\n' + """if 1:
        def f1():
            'docstring'
        def f2():
            'docstring'
            return 'docstring'
        def f3():
            'foo'
            return 'bar'
        class C1():
            'docstring'
        class C2():
            __doc__ = 'docstring'
        class C3():
            field = 'not docstring'
        class C4():
            'docstring'
            field = 'docstring'
        """
        code_w = compile_with_astcompiler(source, 'exec', self.space)
        code_w.remove_docstrings(self.space)
        dict_w = self.space.newdict();
        code_w.exec_code(self.space, dict_w, dict_w)
        self.check(dict_w, expr, result)

    def test_assert_skipping(self):
        space = self.space
        mod = space.getbuiltinmodule('__pypy__')
        w_set_debug = space.getattr(mod, space.wrap('set_debug'))
        space.call_function(w_set_debug, space.w_False)

        source = """if 1:
        assert False
        """
        try:
            self.run(source)
        finally:
            space.call_function(w_set_debug, space.w_True)

    def test_dont_fold_equal_code_objects(self):
        yield self.st, "f=lambda:1;g=lambda:1.0;x=g()", 'type(x)', float
        yield (self.st, "x=(lambda: (-0.0, 0.0), lambda: (0.0, -0.0))[1]()",
                        'repr(x)', '(0.0, -0.0)')

class TestCompilerRevDB(BaseTestCompiler):
    spaceconfig = {"translation.reverse_debugger": True}

    def test_revdb_metavar(self):
        from pypy.interpreter.reverse_debugging import dbstate, setup_revdb
        self.space.reverse_debugging = True
        try:
            setup_revdb(self.space)
            dbstate.standard_code = False
            dbstate.metavars = [self.space.wrap(6)]
            self.simple_test("x = 7*$0", "x", 42)
            dbstate.standard_code = True
            self.error_test("x = 7*$0", SyntaxError)
        finally:
            self.space.reverse_debugging = False


class AppTestCompiler:

    def test_docstring_not_loaded(self):
        import StringIO, dis, sys
        ns = {}
        exec "def f():\n    'hi'" in ns
        f = ns["f"]
        save = sys.stdout
        sys.stdout = output = StringIO.StringIO()
        try:
            dis.dis(f)
        finally:
            sys.stdout = save
        assert "0 ('hi')" not in output.getvalue()

    def test_print_to(self):
        exec """if 1:
        from StringIO import StringIO
        s = StringIO()
        print >> s, "hi", "lovely!"
        assert s.getvalue() == "hi lovely!\\n"
        s = StringIO()
        print >> s, "hi", "lovely!",
        assert s.getvalue() == "hi lovely!"
        """ in {}

    def test_assert_with_tuple_arg(self):
        try:
            assert False, (3,)
        except AssertionError as e:
            assert str(e) == "(3,)"

    # BUILD_LIST_FROM_ARG is PyPy specific
    @py.test.mark.skipif('config.option.runappdirect')
    def test_build_list_from_arg_length_hint(self):
        hint_called = [False]
        class Foo(object):
            def __length_hint__(self):
                hint_called[0] = True
                return 5
            def __iter__(self):
                for i in range(5):
                    yield i
        l = [a for a in Foo()]
        assert hint_called[0]
        assert l == list(range(5))

    def test_unicode_in_source(self):
        import sys
        d = {}
        exec '# -*- coding: utf-8 -*-\n\nu = u"\xf0\x9f\x92\x8b"' in d
        if sys.maxunicode > 65535:
            expected_length = 1
        else:
            expected_length = 2
        assert len(d['u']) == expected_length


class TestOptimizations:
    def count_instructions(self, source):
        code, blocks = generate_function_code(source, self.space)
        instrs = []
        for block in blocks:
            instrs.extend(block.instructions)
        print instrs
        counts = {}
        for instr in instrs:
            counts[instr.opcode] = counts.get(instr.opcode, 0) + 1
        return counts

    def test_elim_jump_to_return(self):
        source = """def f():
        return true_value if cond else false_value
        """
        counts = self.count_instructions(source)
        assert ops.JUMP_FORWARD not in counts
        assert ops.JUMP_ABSOLUTE not in counts
        assert counts[ops.RETURN_VALUE] == 2

    def test_const_fold_subscr(self):
        source = """def f():
        return (0, 1)[0]
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST: 1, ops.RETURN_VALUE: 1}

        source = """def f():
        return (0, 1)[:2]
        """
        # Just checking this doesn't crash out
        self.count_instructions(source)

    def test_const_fold_unicode_subscr(self, monkeypatch):
        source = """def f():
        return u"abc"[0]
        """
        counts = self.count_instructions(source)
        if 0:   # xxx later?
            assert counts == {ops.LOAD_CONST: 1, ops.RETURN_VALUE: 1}

        # getitem outside of the BMP should not be optimized
        source = """def f():
        return u"\U00012345"[0]
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST: 2, ops.BINARY_SUBSCR: 1,
                          ops.RETURN_VALUE: 1}

        source = """def f():
        return u"\U00012345abcdef"[3]
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST: 2, ops.BINARY_SUBSCR: 1,
                          ops.RETURN_VALUE: 1}

        monkeypatch.setattr(optimize, "MAXUNICODE", 0xFFFF)
        source = """def f():
        return u"\uE01F"[0]
        """
        counts = self.count_instructions(source)
        if 0:   # xxx later?
            assert counts == {ops.LOAD_CONST: 1, ops.RETURN_VALUE: 1}
        monkeypatch.undo()

        # getslice is not yet optimized.
        # Still, check a case which yields the empty string.
        source = """def f():
        return u"abc"[:0]
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST: 2, ops.SLICE+2: 1,
                          ops.RETURN_VALUE: 1}

    def test_remove_dead_code(self):
        source = """def f(x):
            return 5
            x += 1
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST:1, ops.RETURN_VALUE: 1}

    def test_remove_dead_jump_after_return(self):
        source = """def f(x, y, z):
            if x:
                return y
            else:
                return z
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_FAST: 3,
                          ops.POP_JUMP_IF_FALSE: 1,
                          ops.RETURN_VALUE: 2}

    def test_remove_dead_yield(self):
        source = """def f(x):
            return
            yield 6
        """
        counts = self.count_instructions(source)
        assert counts == {ops.LOAD_CONST:1, ops.RETURN_VALUE: 1}
        #
        space = self.space
        w_generator = space.appexec([], """():
            d = {}
            exec '''def f(x):
                return
                yield 6
            ''' in d
            return d['f'](5)
        """)
        assert 'generator' in space.str_w(space.repr(w_generator))

    def test_list_comprehension(self):
        source = "def f(): [i for i in l]"
        source2 = "def f(): [i for i in l for j in l]"
        source3 = "def f(): [i for i in l if i]"
        counts = self.count_instructions(source)
        assert ops.BUILD_LIST not in counts
        assert counts[ops.BUILD_LIST_FROM_ARG] == 1
        counts = self.count_instructions(source2)
        assert counts[ops.BUILD_LIST] == 1
        assert ops.BUILD_LIST_FROM_ARG not in counts
        counts = self.count_instructions(source3)
        assert counts[ops.BUILD_LIST] == 1
        assert ops.BUILD_LIST_FROM_ARG not in counts

    def test_folding_of_list_constants(self):
        for source in (
            # in/not in constants with BUILD_LIST should be folded to a tuple:
            'a in [1,2,3]',
            'a not in ["a","b","c"]',
            'a in [None, 1, None]',
            'a not in [(1, 2), 3, 4]',
            ):
            source = 'def f(): %s' % source
            counts = self.count_instructions(source)
            assert ops.BUILD_LIST not in counts
            assert ops.LOAD_CONST in counts

    def test_folding_of_set_constants(self):
        for source in (
            # in/not in constants with BUILD_SET should be folded to a frozenset:
            'a in {1,2,3}',
            'a not in {"a","b","c"}',
            'a in {None, 1, None}',
            'a not in {(1, 2), 3, 4}',
            'a in {1, 2, 3, 3, 2, 1}',
            ):
            source = 'def f(): %s' % source
            counts = self.count_instructions(source)
            assert ops.BUILD_SET not in counts
            assert ops.LOAD_CONST in counts

    def test_dont_fold_huge_powers(self):
        for source in (
            "2 ** 3000",         # not constant-folded: too big
            "(-2) ** 3000",
            ):
            source = 'def f(): %s' % source
            counts = self.count_instructions(source)
            assert ops.BINARY_POWER in counts

        for source in (
            "2 ** 2000",         # constant-folded
            "2 ** -3000",
            "1.001 ** 3000",
            "1 ** 3000.0",
            ):
            source = 'def f(): %s' % source
            counts = self.count_instructions(source)
            assert ops.BINARY_POWER not in counts

    def test_constant_tuples(self):
        source = """def f():
            return ((u"a", 1), 2)
        """
        counts = self.count_instructions(source)
        assert ops.BUILD_TUPLE not in counts
        # also for bytes
        source = """def f():
            return ((b"a", 5), 5, 7, 8)
        """
        counts = self.count_instructions(source)
        assert ops.BUILD_TUPLE not in counts

