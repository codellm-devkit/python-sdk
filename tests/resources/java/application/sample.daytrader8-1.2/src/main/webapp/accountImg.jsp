<!-- 
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
-->
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META http-equiv="Content-Style-Type" content="text/css">
<TITLE>DayTrader Account Information</TITLE>
<LINK rel="stylesheet" href="style.css" type="text/css" />
<link rel="shortcut icon" href="./favicon.ico" />
</HEAD>
<BODY bgcolor="#ffffff" link="#000099" vlink="#000099">
    <%@ page
        import="java.util.Collection, 
                java.util.Iterator, 
                java.math.BigDecimal,com.ibm.websphere.samples.daytrader.entities.OrderDataBean,com.ibm.websphere.samples.daytrader.util.FinancialUtils"
        session="true" isThreadSafe="true" isErrorPage="false"%>
    <jsp:useBean id="results" scope="request" type="java.lang.String" />
    <jsp:useBean id="accountData"
        type="com.ibm.websphere.samples.daytrader.entities.AccountDataBean"
        scope="request" />
    <jsp:useBean id="accountProfileData"
        type="com.ibm.websphere.samples.daytrader.entities.AccountProfileDataBean"
        scope="request" />
    <jsp:useBean id="orderDataBeans" type="java.util.Collection<?>"
        scope="request" />
    <TABLE height="54">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10" colspan=5><B><FONT
                        color="#ffffff">DayTrader Account</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR align="left">
                <TD><A href="app?action=home"><IMG
                        src="images/menuHome.gif" width="80" height="20"
                        border="0" alt=""></A></TD>
                <TD><A href="app?action=account"><IMG
                        src="images/account.gif" width="80" height="20"
                        border="0" alt=""></A></TD>
                <TD><B><A href="app?action=portfolio"><IMG
                            src="images/portfolio.gif" width="80"
                            height="20" border="0" alt=""></A> </B></TD>
                <TD><A
                    href="app?action=quotes&amp;symbols=s:0,s:1,s:2,s:3,s:4"><IMG
                        src="images/quotes.gif" width="80" height="20"
                        border="0" alt=""></A></TD>
                <TD><A href="app?action=logout"><IMG
                        src="images/logout.gif" width="80" height="20"
                        border="0" alt=""></A></TD>
                <TD><IMG src="images/graph.gif" width="32"
                    height="32" border="0" alt=""></TD>
            </TR>
            <TR>
                <TD align="left" colspan="6"><IMG
                    src="images/line.gif" width="600" height="6"
                    border="0" alt=""><FONT color="#ff0000"
                    size="-2"><BR> <%=new java.util.Date()%></FONT></TD>
            </TR>
            <%
                boolean showAllOrders = request.getParameter("showAllOrders") == null ? false : true;
                Collection<?> closedOrders = (Collection<?>) request.getAttribute("closedOrders");
                if ((closedOrders != null) && (closedOrders.size() > 0)) {
            %>
            <TR>
                <TD colspan="6" bgcolor="#ff0000"><BLINK>
                        <B><FONT color="#ffffff">Alert: The
                                following Order(s) have completed.</FONT></B>
                    </BLINK></TD>
            </TR>
            <TR align="center">
                <TD colspan="6">
                    <TABLE border="1" style="font-size: smaller">
                        <TBODY>
                            <%
                                Iterator<?> it = closedOrders.iterator();
                                    while (it.hasNext()) {
                                        OrderDataBean closedOrderData = (OrderDataBean) it.next();
                            %>
                            <TR align="center">
                                <TD><A href="docs/glossary.html">order
                                        ID</A></TD>
                                <TD><A href="docs/glossary.html">order
                                        status</A></TD>
                                <TD><A href="docs/glossary.html">creation
                                        date</A></TD>
                                <TD><A href="docs/glossary.html">completion
                                        date</A></TD>
                                <TD><A href="docs/glossary.html">txn
                                        fee</A></TD>
                                <TD><A href="docs/glossary.html">type</A></TD>
                                <TD><A href="docs/glossary.html">symbol</A></TD>
                                <TD><A href="docs/glossary.html">quantity</A></TD>
                            </TR>
                            <TR align="center">
                                <TD><%=closedOrderData.getOrderID()%></TD>
                                <TD><%=closedOrderData.getOrderStatus()%></TD>
                                <TD><%=closedOrderData.getOpenDate()%></TD>
                                <TD><%=closedOrderData.getCompletionDate()%></TD>
                                <TD><%=closedOrderData.getOrderFee()%></TD>
                                <TD><%=closedOrderData.getOrderType()%></TD>
                                <TD><%=FinancialUtils.printQuoteLink(closedOrderData.getSymbol())%></TD>
                                <TD><%=closedOrderData.getQuantity()%></TD>
                            </TR>
                            <%
                                }
                            %>

                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
            <%
                }
            %>
        </TBODY>
    </TABLE>
    <TABLE width="620">
        <TBODY>
            <TR>
                <TD valign="top" width="643">
                    <TABLE width="100%">
                        <TBODY>
                            <TR>
                                <TD colspan="8"><FONT
                                    color="#ff0000"><%=results%></FONT></TD>
                            </TR>
                            <TR>
                                <TD colspan="8" align="left"
                                    bgcolor="#cccccc"><B>Account
                                        Information</B></TD>
                            </TR>
                            <TR>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">account
                                        created:</A></TD>
                                <TD align="left" valign="bottom"
                                    colspan="2"><%=accountData.getCreationDate()%></TD>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">last
                                        login: </A></TD>
                                <TD align="left" valign="bottom"
                                    colspan="3"><%=accountData.getLastLogin()%></TD>
                                <TD align="left" valign="bottom"></TD>
                            </TR>
                            <TR>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">account
                                        ID</A></TD>
                                <TD valign="bottom"><%=accountData.getAccountID()%></TD>
                                <TD valign="bottom"></TD>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">total
                                        logins: </A></TD>
                                <TD valign="bottom"><%=accountData.getLoginCount()%></TD>
                                <TD valign="bottom"></TD>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">cash
                                        balance: </A></TD>
                                <TD valign="bottom"><%=accountData.getBalance()%></TD>
                            </TR>
                            <TR>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">user
                                        ID:</A></TD>
                                <TD valign="bottom"><%=accountData.getProfileID()%></TD>
                                <TD valign="bottom"></TD>
                                <TD align="right" valign="bottom"><A
                                    href="docs/glossary.html">total
                                        logouts: </A></TD>
                                <TD valign="bottom"><%=accountData.getLogoutCount()%></TD>
                                <TD valign="bottom"></TD>
                                <TD valign="bottom" align="right"><A
                                    href="docs/glossary.html">opening
                                        balance: </A></TD>
                                <TD valign="bottom"><%=accountData.getOpenBalance()%></TD>
                            </TR>
                            <TR>
                                <TD colspan="8"></TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                    <TABLE width="100%">
                        <TBODY>

                            <TR>
                                <TD colspan="5" bgcolor="#cccccc"><B>Total
                                        Orders: </B><%=orderDataBeans.size()%></TD>
                                <TD bgcolor="#cccccc" align="right"><B><A
                                        href="app?action=account&amp;showAllOrders=true">show
                                            all orders</A></B></TD>
                            </TR>
                            <TR align="center">
                                <TD colspan="6">
                                    <TABLE border="1"
                                        style="font-size: smaller">
                                        <CAPTION align="bottom">
                                            <B>Recent Orders</B>
                                        </CAPTION>
                                        <TBODY>
                                            <TR align="center">
                                                <TD><A
                                                    href="docs/glossary.html">order
                                                        ID</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">order
                                                        Status</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">creation
                                                        date</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">completion
                                                        date</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">txn
                                                        fee</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">type</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">symbol</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">quantity</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">price</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">total</A></TD>
                                            </TR>
                                            <%
                                                Iterator<?> it = orderDataBeans.iterator();
                                                int count = 0;
                                                while (it.hasNext()) {
                                                    if ((showAllOrders == false) && (count++ >= 5))
                                                        break;
                                                    OrderDataBean orderData = (OrderDataBean) it.next();
                                            %>
                                            <TR bgcolor="#fafcb6"
                                                align="center">
                                                <TD><%=orderData.getOrderID()%></TD>
                                                <TD><%=orderData.getOrderStatus()%></TD>
                                                <TD><%=orderData.getOpenDate()%></TD>
                                                <TD><%=orderData.getCompletionDate()%></TD>
                                                <TD><%=orderData.getOrderFee()%></TD>
                                                <TD><%=orderData.getOrderType()%></TD>
                                                <TD><%=FinancialUtils.printQuoteLink(orderData.getSymbol())%></TD>
                                                <TD><%=orderData.getQuantity()%></TD>
                                                <TD><%=orderData.getPrice()%></TD>
                                                <TD><%=orderData.getPrice().multiply(new BigDecimal(orderData.getQuantity()))%></TD>
                                            </TR>
                                            <%
                                                }
                                            %>
                                        </TBODY>
                                    </TABLE>
                                </TD>
                            </TR>
                            <TR>
                                <TD colspan="6"></TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                    <FORM>
                        <TABLE width="100%">
                            <TBODY>
                                <TR>
                                    <TD colspan="6" bgcolor="#cccccc"><B>Account
                                            Profile</B></TD>
                                </TR>
                                <TR>
                                    <TD align="right" valign="top"
                                        width="113"><A
                                        href="docs/glossary.html">user
                                            ID:</A></TD>
                                    <TD align="left" valign="top"
                                        colspan="2" width="228"><INPUT
                                        size="30" type="text"
                                        maxlength="30" readonly
                                        name="userID"
                                        value="<%=accountProfileData.getUserID()%>"></TD>
                                    <TD align="right" valign="top"
                                        width="73" colspan="2"><A
                                        href="docs/glossary.html">full
                                            name: </A></TD>
                                    <TD align="left" valign="top"><INPUT
                                        size="30" type="text"
                                        maxlength="30" name="fullname"
                                        value="<%=accountProfileData.getFullName()%>"></TD>
                                </TR>
                                <TR>
                                    <TD align="right" width="113"><A
                                        href="docs/glossary.html">password:
                                    </A></TD>
                                    <TD colspan="2" width="228"><INPUT
                                        size="30" type="password"
                                        maxlength="30" name="password"
                                        value="<%=accountProfileData.getPassword()%>"></TD>
                                    <TD align="right" width="73"
                                        colspan="2"><A
                                        href="docs/glossary.html">address:
                                    </A></TD>
                                    <TD><INPUT size="30"
                                        type="text" maxlength="30"
                                        name="address"
                                        value="<%=accountProfileData.getAddress()%>"></TD>
                                </TR>
                                <TR>
                                    <TD align="right" width="113"><A
                                        href="docs/glossary.html">confirm
                                            password: </A><BR></TD>
                                    <TD colspan="2" align="left"
                                        width="228"><INPUT
                                        size="30" type="password"
                                        maxlength="30" name="cpassword"
                                        value="<%=accountProfileData.getPassword()%>"></TD>
                                    <TD align="right" width="73"
                                        colspan="2"><A
                                        href="docs/glossary.html">credit
                                            card: </A></TD>
                                    <TD align="left"><INPUT
                                        size="30" type="text"
                                        maxlength="30" name="creditcard"
                                        value="<%=accountProfileData.getCreditCard()%>"
                                        readonly></TD>
                                </TR>
                                <TR>
                                    <TD align="right" width="113"><A
                                        href="docs/glossary.html">email
                                            address: </A></TD>
                                    <TD colspan="2" align="left"
                                        width="228"><INPUT
                                        size="30" type="text"
                                        maxlength="30" name="email"
                                        value="<%=accountProfileData.getEmail()%>"></TD>
                                    <TD align="right" width="73"
                                        colspan="2"></TD>
                                    <TD align="center"><INPUT
                                        type="submit" name="action"
                                        value="update_profile"></TD>
                                </TR>
                                <TR>
                                    <TD width="113"></TD>
                                    <TD colspan="5"></TD>
                                </TR>
                            </TBODY>
                        </TABLE>
                    </FORM>
                </TD>
            </TR>
        </TBODY>
    </TABLE>
    <TABLE height="54" style="font-size: smaller">
        <TBODY>
            <TR>
                <TD colspan="2">
                    <HR>
                </TD>
            </TR>
            <TR>
                <TD colspan="2">
                    <TABLE width="100%" style="font-size: smaller">
                        <TBODY>
                            <TR>
                                <TD>Note: Click any <A
                                    href="docs/glossary.html">symbol</A>
                                    for a quote or to trade.
                                </TD>
                                <TD align="right">
                                    <FORM action="">
                                        <INPUT type="submit"
                                            name="action" value="quotes">
                                        <INPUT size="20" type="text"
                                            name="symbols"
                                            value="s:0, s:1, s:2, s:3, s:4">
                                    </FORM>
                                </TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10"><B><FONT color="#ffffff">DayTrader
                            Account</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR>
                <TD colspan="4" align="center"><IMG
                    src="images/ticker-anim.gif" width="385" height="22"
                    border="0" align="middle" alt=""></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
