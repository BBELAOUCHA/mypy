from nodes import NodeVisitor, SymbolTable, Node, MypyFile, VarDef, LDEF, Var, OverloadedFuncDef, FuncDef, FuncItem, Annotation, FuncBase, TypeInfo, TypeDef, GDEF, Block, AssignmentStmt, NameExpr, MemberExpr, IndexExpr, TupleExpr, ListExpr, ParenExpr, ExpressionStmt, ReturnStmt, IfStmt, WhileStmt, OperatorAssignmentStmt, YieldStmt, WithStmt, AssertStmt, RaiseStmt, TryStmt, ForStmt, DelStmt, CallExpr, IntExpr, StrExpr, FloatExpr, OpExpr, UnaryExpr, CastExpr, SuperExpr, TypeApplication, DictExpr, SliceExpr, FuncExpr, TempNode, SymbolTableNode
from types import Typ, Any, Callable, Void, FunctionLike, Overloaded, TupleType, Instance, is_same_type, NoneType, UnboundType
from errors import Errors


# Map from binary operator id to related method name.
dict<str, str> op_methods = {'+': '__add__', '-': '__sub__', '*': '__mul__', '/': '__truediv__', '%': '__mod__', '//': '__floordiv__', '**': '__pow__', '&': '__and__', '|': '__or__', '^': '__xor__', '<<': '__lshift__', '>>': '__rshift__', '==': '__eq__', '!=': '__ne__', '<': '__lt__', '>=': '__ge__', '>': '__gt__', '<=': '__le__', 'in': '__contains__'}


# Mypy type checker. Type check Mypy source files that have been
# semantically analysed.
class TypeChecker(NodeVisitor<Typ>):
    Errors errors          # Error reporting
    SymbolTable symtable     # Symbol table for the whole program
    MessageBuilder msg  # Utility for generating messages
    dict<Node, Typ> type_map  # Types of type checked nodes
    ExpressionChecker expr_checker
    
    list<str> stack = [None] # Stack of local variable definitions;
    # nil separates nested functions
    list<Typ> return_types = [] # Stack of function return types
    list<Typ> type_context = [] # Type context for type inference
    list<bool> dynamic_funcs = [] # Flags; true for dynamically 
    # typed functions
    
    SymbolTable globals
    SymbolTable class_tvars
    SymbolTable locals
    dict<str, MypyFile> modules
    
    # Construct a type checker. Use errors to report type check errors. Assume
    # symtable has been populated by the semantic analyzer.
    void __init__(self, Errors errors, dict<str, MypyFile> modules):
        self.expr_checker
        self.errors = errors
        self.modules = modules
        self.msg = MessageBuilder(errors)
        self.type_map = {}
        self.expr_checker = ExpressionChecker(self, self.msg)
    
    # Type check a mypy file with the given path.
    void visit_file(self, MypyFile file_node, str path):  
        self.errors.set_file(path)
        self.globals = file_node.names
        self.locals = None
        self.class_tvars = None
        
        for d in file_node.defs:
            self.accept(d)
    
    # Type check a node in the given type context.
    Typ accept(self, Node node, Typ type_context=None):
        self.type_context.append(type_context)
        typ = node.accept(self)
        self.type_context.remove_at(-1)
        self.store_type(node, typ)
        if self.is_dynamic_function():
            return Any()
        else:
            return typ
    
    
    # Definitions
    #------------
    
    
    # Type check a variable definition (of any kind: local, member or local).
    Typ visit_var_def(self, VarDef defn):
        # Type check initializer.
        if defn.init is not None:
            # There is an initializer.
            if defn.items[0][1] is not None:
                # Explicit types.
                if len(defn.items) == 1:
                    self.check_assignment(defn.items[0][1], None, defn.init, defn.init)
                else:
                    # Multiple assignment.
                    list<Typ> lvt = []
                    for v, t in defn.items:
                        lvt.append(t)
                    self.check_multi_assignment(lvt, <tuple<Typ, Node>> [None] * len(lvt), defn.init, defn.init)
            else:
                init_type = self.accept(defn.init)
                if defn.kind == LDEF and not defn.is_top_level:
                    # Infer local variable type if there is an initializer except if the
                    # definition is at the top level (outside a function).
                    list<Var> names = []
                    for v, t in defn.items:
                        names.append(v)
                    self.infer_local_variable_type(names, init_type, defn)
        else:
            # No initializer
            if defn.kind == LDEF and defn.items[0][1] is None and not defn.is_top_level and not self.is_dynamic_function():
                self.fail(NEED_ANNOTATION_FOR_VAR, defn)
    
    def infer_local_variable_type(self, x, y, z):
        # TODO
        raise RuntimeError('Not implemented')
    
    Typ visit_overloaded_func_def(self, OverloadedFuncDef defn):
        for fdef in defn.items:
            self.check_func_item(fdef)
        if defn.info is not None:
            self.check_method_override(defn)
    
    # Type check a function definition.
    Typ visit_func_def(self, FuncDef defn):
        self.check_func_item(defn)
        if defn.info is not None:
            self.check_method_override(defn)
    
    Typ check_func_item(self, FuncItem defn):
        # We may be checking a function definition or an anonymous function. In
        # the first case, set up another reference with the precise type.
        FuncDef fdef = None
        if isinstance(defn, FuncDef):
            fdef = (FuncDef)defn
        
        self.dynamic_funcs.append(defn.typ is None)
        
        if fdef is not None:
            self.errors.set_function(fdef.name)
        
        typ = function_type(defn)
        if isinstance(typ, Callable):
            self.check_func_def(defn, typ)
        else:
            raise RuntimeError('Not supported')
        
        if fdef is not None:
            self.errors.set_function(None)
        
        self.dynamic_funcs.remove_at(-1)
    
    # Check a function definition.
    void check_func_def(self, FuncItem defn, Typ typ):
        # We may be checking a function definition or an anonymous function. In
        # the first case, set up another reference with the precise type.
        FuncDef fdef = None
        if isinstance(defn, FuncDef):
            fdef = (FuncDef)defn
        
        self.enter()
        
        if fdef is not None:
            # The cast below will work since non-method create will cause semantic
            # analysis to fail, and type checking won't be done.
            if fdef.info is not None and fdef.name == '__init__' and not isinstance(((Callable)typ).ret_type, Void) and not self.dynamic_funcs[-1]:
                self.fail(INIT_MUST_NOT_HAVE_RETURN_TYPE, defn.typ)
        
        # Push return type.
        self.return_types.append(((Callable)typ).ret_type)
        
        # Add arguments to symbol table.
        ctype = (Callable)typ
        nargs = len(defn.args)
        for i in range(len(ctype.arg_types)):
            arg_type = ctype.arg_types[i]
            if defn.var_arg is not None and i == nargs:
                arg_type = self.named_generic_type('builtins.list', [arg_type])
                defn.var_arg.typ = Annotation(arg_type)
            else:
                defn.args[i].typ = Annotation(arg_type)
        
        # Type check initialization expressions.
        for i in range(len(defn.init)):
            if defn.init[i] is not None:
                self.accept(defn.init[i])
        
        # Type check body.
        self.accept(defn.body)
        
        # Pop return type.
        self.return_types.remove_at(-1)
        
        self.leave()
    
    # Check that function definition is compatible with any overridden
    # definitions defined in superclasses or implemented interfaces.
    void check_method_override(self, FuncBase defn):
        # Check against definitions in superclass.
        self.check_method_or_accessor_override_for_base(defn, defn.info.base)
        # Check against definitions in implemented interfaces.
        for iface in defn.info.interfaces:
            self.check_method_or_accessor_override_for_base(defn, iface)
    
    # Check that function definition is compatible with any overridden
    # definition in the specified supertype.
    void check_method_or_accessor_override_for_base(self, FuncBase defn, TypeInfo base):
        if base is not None:
            if defn.name != '__init__':
                # Check method override (create is special).
                base_method = base.get_method(defn.name)
                if base_method is not None and base_method.info == base:
                    # There is an overridden method in the supertype.
                    
                    # Construct the type of the overriding method.
                    typ = method_type(defn)
                    # Map the overridden method type to subtype context so that it
                    # can be checked for compatibility. Note that multiple types from
                    # multiple implemented interface instances may be present.
                    original_type = map_type_from_supertype(method_type(base_method), defn.info, base)
                    # Check that the types are compatible.
                    # TODO overloaded signatures
                    self.check_override((FunctionLike)typ, (FunctionLike)original_type, defn.name, base_method.info.name, defn)
            
            # Also check interface implementations.
            for iface in base.interfaces:
                self.check_method_or_accessor_override_for_base(defn, iface)
            
            # We have to check that the member is compatible with all supertypes
            # due to the dynamic type. Otherwise we could first override with
            # dynamic and then with an arbitary type.
            self.check_method_or_accessor_override_for_base(defn, base.base)
    
    # Check a method override with given signatures.
    #
    #  override:  The signature of the overriding method.
    #  original:  The signature of the original supertype method.
    #  name:      The name of the subtype. This and the next argument are
    #             only used for generating error messages.
    #  supertype: The name of the supertype.
    void check_override(self, FunctionLike override, FunctionLike original, str name, str supertype, Context node):
        if isinstance(override, Overloaded) or isinstance(original, Overloaded) or len(((Callable)override).arg_types) != len(((Callable)original).arg_types) or ((Callable)override).min_args != ((Callable)original).min_args:
            if not is_subtype(override, original):
                self.msg.signature_incompatible_with_supertype(name, supertype, node)
            return 
        else:
            # Give more detailed messages for the common case of both signatures
            # having the same number of arguments and no intersection types.
            
            coverride = (Callable)override
            coriginal = (Callable)original
            
            for i in range(len(coverride.arg_types)):
                if not is_equivalent(coriginal.arg_types[i], coverride.arg_types[i]):
                    self.msg.argument_incompatible_with_supertype(i + 1, name, supertype, node)
            
            if not is_subtype(coverride.ret_type, coriginal.ret_type):
                self.msg.return_type_incompatible_with_supertype(name, supertype, node)
    
    # Type check a type definition (class or interface).
    Typ visit_type_def(self, TypeDef defn):
        typ = self.lookup(defn.name, GDEF).node
        # TODO
        #addMembersToSymbolTable(type as TypeInfo)
        
        self.errors.set_type(defn.name, defn.is_interface)
        
        self.check_unique_interface_implementations((TypeInfo)typ)
        
        self.check_interface_errors((TypeInfo)typ)
        
        self.accept(defn.defs)
        
        self.errors.set_type(None, False)
    
    # Check that each interface is implemented only once.
    void check_unique_interface_implementations(self, TypeInfo typ):
        ifaces = typ.interfaces[:]
        
        dup = find_duplicate(ifaces)
        if dup is not None:
            self.msg.duplicate_interfaces(typ, dup)
            return 
        
        base = typ.base
        while base is not None:
            # Avoid duplicate error messages.
            if find_duplicate(base.interfaces) is not None:
                return 
            
            ifaces.extend(base.interfaces)
            dup = find_duplicate(ifaces)
            if dup is not None:
                self.msg.duplicate_interfaces(typ, dup)
                return 
            base = base.base
    
    void check_interface_errors(self, TypeInfo typ):
        interfaces = typ.all_directly_implemented_interfaces()
        for iface in interfaces:
            for n in iface.methods.keys():
                if not typ.has_method(n):
                    self.msg.interface_member_not_implemented(typ, iface, n)
    
    
    # Statements
    #-----------
    
    
    Typ visit_block(self, Block b):
        for s in b.body:
            self.accept(s)
    
    # Type check an assignment statement. Handle all kinds of assignment
    # statements (simple, indexed, multiple).
    Typ visit_assignment_stmt(self, AssignmentStmt s):
        # TODO support chained assignment x = y = z
        if len(s.lvalues) > 1:
            self.fail('Chained assignment not supported yet', s)
        
        # Collect lvalue types. Index lvalues require special consideration,
        # since we cannot typecheck them until we known the rvalue type.
        list<Typ> lvalue_types = []    # May be nil
        # Base type and index types (or nil)
        list<tuple<Typ, Node>> index_lvalue_types = []
        list<Var> inferred = []
        is_inferred = False
        
        lvalues = self.expand_lvalues(s.lvalues[0])
        for lv in lvalues:
            if self.is_definition(lv):
                is_inferred = True
                if isinstance(lv, NameExpr):
                    n = (NameExpr)lv
                    inferred.append(((Var)n.node))
                else:
                    m = (MemberExpr)lv
                    inferred.append(m.def_var)
                lvalue_types.append(None)
                index_lvalue_types.append(None)
            elif isinstance(lv, IndexExpr):
                ilv = (IndexExpr)lv
                lvalue_types.append(None)
                index_lvalue_types.append((self.accept(ilv.base), ilv.index))
            else:
                lvalue_types.append(self.accept(lv))
                index_lvalue_types.append(None)
        
        if len(lvalues) == 1:
            # Single lvalue.
            self.check_assignment(lvalue_types[0], index_lvalue_types[0], s.rvalue, s.rvalue)
        else:
            self.check_multi_assignment(lvalue_types, index_lvalue_types, s.rvalue, s.rvalue)
        if is_inferred:
            self.infer_variable_type(inferred, self.accept(s.rvalue), s.rvalue)
    
    def is_definition(self, s):
        return (isinstance(s, NameExpr) or isinstance(s, MemberExpr)) and s.is_def
    
    list<Node> expand_lvalues(self, Node n):
        if isinstance(n, TupleExpr):
            return self.expr_checker.unwrap_list(((TupleExpr)n).items)
        elif isinstance(n, ListExpr):
            return self.expr_checker.unwrap_list(((ListExpr)n).items)
        elif isinstance(n, ParenExpr):
            return self.expand_lvalues(((ParenExpr)n).expr)
        else:
            return [n]
    
    # Infer the type of initialized local variables from the type of the
    # initializer expression.
    void infer_variable_type(self, list<Var> names, Typ init_type, Context context):
        if isinstance(init_type, Void):
            self.check_not_void(init_type, context)
        elif not self.is_valid_inferred_type(init_type):
            # We cannot use the type of the initialization expression for type
            # inference (it's not specific enough).
            self.fail(NEED_ANNOTATION_FOR_VAR, context)
        else:
            # Infer type of the target.
            
            # Make the type more general (strip away function names etc.).
            init_type = self.strip_type(init_type)
            
            if len(names) > 1:
                if isinstance(init_type, TupleType):
                    tinit_type = (TupleType)init_type
                    # Initializer with a tuple type.
                    if len(tinit_type.items) == len(names):
                        for i in range(len(names)):
                            names[i].typ = Annotation(tinit_type.items[i], -1)
                    else:
                        self.fail(INCOMPATIBLE_TYPES_IN_ASSIGNMENT, context)
                elif isinstance(init_type, Instance) and ((Instance)init_type).typ.full_name == 'builtins.list':
                    # Initializer with an array type.
                    item_type = ((Instance)init_type).args[0]
                    for i in range(len(names)):
                        names[i].typ = Annotation(item_type, -1)
                elif isinstance(init_type, Any):
                    for i in range(len(names)):
                        names[i].typ = Annotation(Any(), -1)
                else:
                    self.fail(INCOMPATIBLE_TYPES_IN_ASSIGNMENT, context)
            else:
                for v in names:
                    v.typ = Annotation(init_type, -1)
    
    # Is an inferred type invalid (e.g. the nil type or a type with a nil
    # component)?
    bool is_valid_inferred_type(self, Typ typ):
        if is_same_type(typ, NoneType()):
            return False
        elif isinstance(typ, Instance):
            for arg in ((Instance)typ).args:
                if not self.is_valid_inferred_type(arg):
                    return False
        elif isinstance(typ, TupleType):
            for item in ((TupleType)typ).items:
                if not self.is_valid_inferred_type(item):
                    return False
        return True
    
    # Remove a copy of type with all "debugging information" (e.g. name of
    # function) removed.
    Typ strip_type(self, Typ typ):
        if isinstance(typ, Callable):
            ctyp = (Callable)typ
            return Callable(ctyp.arg_types, ctyp.min_args, ctyp.is_var_arg, ctyp.ret_type, ctyp.is_type_obj, None, ctyp.variables)
        else:
            return typ
    
    void check_multi_assignment(self, list<Typ> lvalue_types, list<tuple<Typ, Node>> index_lvalue_types, Node rvalue, Context context, str msg=INCOMPATIBLE_TYPES_IN_ASSIGNMENT):
        rvalue_type = self.accept(rvalue) # TODO maybe do this elsewhere; redundant
        # Try to expand rvalue to lvalue(s).
        if isinstance(rvalue_type, Any):
            pass
        elif isinstance(rvalue_type, TupleType):
            # Rvalue with tuple type.
            trvalue = (TupleType)rvalue_type
            list<Typ> items = []
            for i in range(len(lvalue_types)):
                if lvalue_types[i] is not None:
                    items.append(lvalue_types[i])
                elif i < len(trvalue.items):
                    # TODO Figure out more precise type context, probably based on the
                    #      type signature of the _set method.
                    items.append(trvalue.items[i])
            trvalue = ((TupleType)self.accept(rvalue, TupleType(items)))
            if len(trvalue.items) != len(lvalue_types):
                self.msg.incompatible_value_count_in_assignment(len(lvalue_types), len(trvalue.items), context)
            else:
                # The number of values is compatible. Check their types.
                for i in range(len(lvalue_types)):
                    self.check_assignment(lvalue_types[i], index_lvalue_types[i], self.temp_node(trvalue.items[i]), context, msg)
        elif isinstance(rvalue_type, Instance) and ((Instance)rvalue_type).typ.full_name == 'builtins.list':
            # Rvalue with Array type.
            item_type = ((Instance)rvalue_type).args[0]
            for i in range(len(lvalue_types)):
                self.check_assignment(lvalue_types[i], index_lvalue_types[i], self.temp_node(item_type), context, msg)
        else:
            self.fail(msg, context)
    
    void check_assignment(self, Typ lvalue_type, tuple<Typ, Node> index_lvalue, Node rvalue, Context context, str msg=INCOMPATIBLE_TYPES_IN_ASSIGNMENT):
        if lvalue_type is not None:
            rvalue_type = self.accept(rvalue, lvalue_type)      
            self.check_subtype(rvalue_type, lvalue_type, context, msg)
        elif index_lvalue is not None:
            self.check_indexed_assignment(index_lvalue, rvalue, context)
    
    # Type check indexed assignment base[index] = rvalue. The lvalueTypes
    # argument is the tuple (base type, index), the rvaluaType is the type
    # of the rvalue.
    Typ check_indexed_assignment(self, tuple<Typ, Node> lvalue, Node rvalue, Context context):
        method_type = self.expr_checker.analyse_external_member_access('__setitem__', lvalue[0], context)
        return self.expr_checker.check_call(method_type, [lvalue[1], rvalue], context)
    
    Typ visit_expression_stmt(self, ExpressionStmt s):
        self.accept(s.expr)
    
    # Type check a return statement.
    Typ visit_return_stmt(self, ReturnStmt s):
        if self.is_within_function():
            if s.expr is not None:
                # Return with a value.
                typ = self.accept(s.expr, self.return_types[-1])
                # Returning a value of type dynamic is always fine.
                if not isinstance(typ, Any):
                    if isinstance(self.return_types[-1], Void):
                        self.fail(NO_RETURN_VALUE_EXPECTED, s)
                    else:
                        self.check_subtype(typ, self.return_types[-1], s, INCOMPATIBLE_RETURN_VALUE_TYPE)
            else:
                # Return without a value.
                if not isinstance(self.return_types[-1], Void) and not self.is_dynamic_function():
                    self.fail(RETURN_VALUE_EXPECTED, s)
    
    # Type check an if statement.
    Typ visit_if_stmt(self, IfStmt s):
        for e in s.expr:
            t = self.accept(e)
            self.check_not_void(t, e)
        for b in s.body:
            self.accept(b)
        if s.else_body is not None:
            self.accept(s.else_body)
    
    # Type check a while statement.
    Typ visit_while_stmt(self, WhileStmt s):
        t = self.accept(s.expr)
        self.check_not_void(t, s)
        self.accept(s.body)
        if s.else_body is not None:
            self.accept(s.else_body)
    
    # Type check an operator assignment statement, e.g. x += 1.
    Typ visit_operator_assignment_stmt(self, OperatorAssignmentStmt s):
        lvalue_type = self.accept(s.lvalue)
        rvalue_type = self.expr_checker.check_op(op_methods[s.op], lvalue_type, s.rvalue, s)
        
        if isinstance(s.lvalue, IndexExpr):
            lv = (IndexExpr)s.lvalue
            self.check_assignment(None, (self.accept(lv.base), lv.index), s.rvalue, s.rvalue)
        else:
            if not is_subtype(rvalue_type, lvalue_type):
                self.msg.incompatible_operator_assignment(s.op, s)
    
    Typ visit_yield_stmt(self, YieldStmt s):
        self.msg.not_implemented('yield statement', s)
    
    Typ visit_with_stmt(self, WithStmt s):
        self.msg.not_implemented('with statement', s)
    
    Typ visit_assert_stmt(self, AssertStmt s):
        self.accept(s.expr)
    
    # Type check a raise statement.
    Typ visit_raise_stmt(self, RaiseStmt s):
        typ = self.accept(s.expr)
        self.check_subtype(typ, self.named_type('builtins.BaseException'), s, INVALID_EXCEPTION_TYPE)
    
    # Type check a try statement.
    Typ visit_try_stmt(self, TryStmt s):
        self.accept(s.body)
        for i in range(len(s.handlers)):
            if s.types[i] is not None:
                t = self.exception_type(s.types[i])
                if s.vars[i] is not None:
                    s.vars[i].typ = Annotation(t)
            self.accept(s.handlers[i])
        if s.finally_body is not None:
            self.accept(s.finally_body)
        if s.else_body is not None:
            self.accept(s.else_body)
    
    Typ exception_type(self, Node n):
        if isinstance(n, NameExpr) and isinstance(((NameExpr)n).node, TypeInfo):
            return Instance((TypeInfo)((NameExpr)n).node, [])
        elif isinstance(self.expr_checker.unwrap(n), TupleExpr):
            self.fail('Multiple exception types not supported yet', n)
        else:
            self.fail('Unsupported exception type', n)
            return Any()
    
    # Type check a for statement.
    Typ visit_for_stmt(self, ForStmt s):
        iterable = self.accept(s.expr)
        
        self.check_not_void(iterable, s.expr)
        self.check_subtype(iterable, self.named_generic_type('builtins.iterable', [Any()]), s.expr, ITERABLE_EXPECTED)
        
        Typ method
        
        echk = self.expr_checker
        method = echk.analyse_external_member_access('__iter__', iterable, s.expr)
        iterator = echk.check_call(method, [], s.expr)
        method = echk.analyse_external_member_access('__next__', iterator, s.expr)
        item = echk.check_call(method, [], s.expr)
        
        if not s.is_annotated():
            self.infer_variable_type(s.index, item, s)
        
        if len(s.index) == 1:
            if s.index[0].typ is not None:
                self.check_assignment(s.index[0].typ.typ, None, self.temp_node(item), s, INCOMPATIBLE_TYPES_IN_FOR)
        else:
            list<Typ> t = []
            for index in s.index:
                if index.typ is not None:
                    t.append(index.typ.typ)
                else:
                    t.append(Any())
            self.check_multi_assignment(t, <tuple<Typ, Node>> [None] * len(s.types), self.temp_node(item), s.expr, INCOMPATIBLE_TYPES_IN_FOR)
        
        self.accept(s.body)
    
    Typ visit_del_stmt(self, DelStmt s):
        if isinstance(s.expr, IndexExpr):
            e = (IndexExpr)s.expr  # Cast
            m = MemberExpr(e.base, '__delitem__')
            m.line = s.line
            c = CallExpr(m, [e.index])
            c.line = s.line
            return c.accept(self)
        else:
            return None # this case is handled in semantical analysis
    
    
    # Expressions
    # -----------
    
    
    Typ visit_name_expr(self, NameExpr e):
        return self.expr_checker.visit_name_expr(e)
    
    Typ visit_paren_expr(self, ParenExpr e):
        return self.expr_checker.visit_paren_expr(e)
    
    Typ visit_call_expr(self, CallExpr e):
        return self.expr_checker.visit_call_expr(e)
    
    Typ visit_member_expr(self, MemberExpr e):
        return self.expr_checker.visit_member_expr(e)
    
    Typ visit_int_expr(self, IntExpr e):
        return self.expr_checker.visit_int_expr(e)
    
    Typ visit_str_expr(self, StrExpr e):
        return self.expr_checker.visit_str_expr(e)
    
    Typ visit_float_expr(self, FloatExpr e):
        return self.expr_checker.visit_float_expr(e)
    
    Typ visit_op_expr(self, OpExpr e):
        return self.expr_checker.visit_op_expr(e)
    
    Typ visit_unary_expr(self, UnaryExpr e):
        return self.expr_checker.visit_unary_expr(e)
    
    Typ visit_index_expr(self, IndexExpr e):
        return self.expr_checker.visit_index_expr(e)
    
    Typ visit_cast_expr(self, CastExpr e):
        return self.expr_checker.visit_cast_expr(e)
    
    Typ visit_super_expr(self, SuperExpr e):
        return self.expr_checker.visit_super_expr(e)
    
    Typ visit_type_application(self, TypeApplication e):
        return self.expr_checker.visit_type_application(e)
    
    Typ visit_list_expr(self, ListExpr e):
        return self.expr_checker.visit_list_expr(e)
    
    Typ visit_tuple_expr(self, TupleExpr e):
        return self.expr_checker.visit_tuple_expr(e)
    
    Typ visit_dict_expr(self, DictExpr e):
        return self.expr_checker.visit_dict_expr(e)
    
    Typ visit_slice_expr(self, SliceExpr e):
        return self.expr_checker.visit_slice_expr(e)
    
    Typ visit_func_expr(self, FuncExpr e):
        return self.expr_checker.visit_func_expr(e)
    
    Typ visit_temp_node(self, TempNode e):
        return e.typ
    
    
    # Helpers
    # -------
    
    
    # Generate an error if the subtype is not compatible with supertype.
    void check_subtype(self, Typ subtype, Typ supertype, Context context, str msg=INCOMPATIBLE_TYPES):
        if not is_subtype(subtype, supertype):
            if isinstance(subtype, Void):
                self.msg.does_not_return_value(subtype, context)
            else:
                self.fail(msg, context)
    
    # Return an instance type with type given by the name and no type arguments.
    # For example, namedType("builtins.object") produces the object type.
    Instance named_type(self, str name):
        # Assume that the name refers to a type.
        sym = self.lookup_qualified(name)
        return Instance((TypeInfo)sym.node, [])
    
    # Return named instance type, or UnboundType if the type was not defined.
    #
    # This is used to simplify test cases by avoiding the need to define basic
    # types not needed in specific test cases (tuple etc.).
    Typ named_type_if_exists(self, str name):
        try:
            # Assume that the name refers to a type.
            sym = self.lookup_qualified(name)
            return Instance((TypeInfo)sym.node, [])
        except KeyError:
            return UnboundType(name)
    
    # Return an instance with the given name and type arguments. Assume that
    # the number of arguments is correct.
    Instance named_generic_type(self, str name, list<Typ> args):
        # Assume that the name refers to a compatible generic type.
        sym = self.lookup_qualified(name)
        return Instance((TypeInfo)sym.node, args)
    
    # Return instance type 'type'.
    Instance type_type(self):
        return self.named_type('builtins.type')
    
    # Return instance type 'object'.
    Instance object_type(self):
        return self.named_type('builtins.object')
    
    # Return instance type 'bool'.
    Instance bool_type(self):
        return self.named_type('builtins.bool')
    
    # Return instance type 'tuple'.
    Typ tuple_type(self):
        # We need the tuple for analysing member access. We want to be able to do
        # this even if tuple type is not available (useful in test cases), so we
        # return an unbound type if there is no tuple type.
        return self.named_type_if_exists('builtins.tuple')
    
    # Generate an error if the types are not equivalent. The dynamic type is
    # equivalent with all types.
    void check_type_equivalency(self, Typ t1, Typ t2, Context node, str msg=INCOMPATIBLE_TYPES):
        if not is_equivalent(t1, t2):
            self.fail(msg, node)
    
    # Store the type of a node in the type map.
    void store_type(self, Node node, Typ typ):
        self.type_map[node] = typ
    
    bool is_dynamic_function(self):
        return len(self.dynamic_funcs) > 0 and self.dynamic_funcs[-1]
    
    # Look up a definition from the symbol table with the given name.
    # TODO remove kind argument
    SymbolTableNode lookup(self, str name, Constant kind):
        if self.locals is not None and self.locals.has_key(name):
            return self.locals[name]
        elif self.class_tvars is not None and self.class_tvars.has_key(name):
            return self.class_tvars[name]
        elif self.globals.has_key(name):
            return self.globals[name]
        else:
            b = self.globals.get('__builtins__', None)
            if b is not None:
                table = ((MypyFile)b.node).names
                if table.has_key(name):
                    return table[name]
            raise KeyError('Failed lookup: {}'.format(name))
    
    SymbolTableNode lookup_qualified(self, str name):
        if '.' not in name:
            return self.lookup(name, GDEF) # FIX kind
        else:
            parts = name.split('.')
            n = self.modules[parts[0]]
            for i in range(1, len(parts) - 1):
                n = (MypyFile)((n.names.get(parts[i], None).node))
            return n.names[parts[-1]]
    
    void enter(self):
        self.locals = SymbolTable()
    
    void leave(self):
        self.locals = None
    
    # Return a BasicTypes instance that contains primitive types that are
    # needed for certain type operations (joins, for example).
    BasicTypes basic_types(self):
        # TODO function type
        return BasicTypes(self.object_type(), self.type_type(), self.named_type_if_exists('builtins.tuple'), self.named_type_if_exists('builtins.function'))
    
    # Are we currently type checking within a function (i.e. not at class body
    # or at the top level)?
    bool is_within_function(self):
        return self.return_types != []
    
    # Generate an error if the type is Void.
    void check_not_void(self, Typ typ, Context context):
        if isinstance(typ, Void):
            self.msg.does_not_return_value(typ, context)
    
    # Create a temporary node with the given, fixed type.
    Node temp_node(self, Typ t):
        return TempNode(t)
    
    
    # Error messages
    #---------------
    
    
    # Produce an error message.
    void fail(self, str msg, Context context):
        self.msg.fail(msg, context)
