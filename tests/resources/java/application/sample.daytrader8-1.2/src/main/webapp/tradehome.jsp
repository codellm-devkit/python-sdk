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
<TITLE>Welcome to DayTrader</TITLE>
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
    <jsp:useBean id="holdingDataBeans" type="java.util.Collection<?>"
        scope="request" />
    <TABLE height="54">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10" colspan=6><B><FONT
                        color="#ffffff">DayTrader Home</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR align="left">
                <TD><B><A href="app?action=home">Home</A></B></TD>
                <TD><B><A href="app?action=account">Account</A></B></TD>
                <TD><B><A href="app?action=mksummary">Market Summary</A></B></TD>
                <TD><B><A href="app?action=portfolio">Portfolio</A></B></TD>
                <TD><B><A href="app?action=quotes&amp;symbols=s:0,s:1,s:2,s:3,s:4">Quotes/Trade</A></B></TD>
                <TD><B><A href="app?action=logout">Logoff</A></B></TD>
                <TD></TD>
            </TR>
            <TR>
                <TD align="right" colspan="7">
                    <HR> <FONT color="#ff0000" size="-2"><%=new java.util.Date()%></FONT>
                </TD>
            </TR>
            <%
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
    <TABLE width="636">
        <TBODY>
            <TR>
                <TD valign="top" width="377">
                    <TABLE width="384">
                        <TBODY>
                            <TR>
                                <TD colspan="3"><B>Welcome
                                        &nbsp;<%=accountData.getProfileID()%>,
                                </B></TD>
                            </TR>
                            <TR>
                                <TD width="133"></TD>
                                <TD width="22"></TD>
                                <TD width="212"></TD>
                            </TR>
                            <TR>
                                <TD colspan="3" align="left"
                                    bgcolor="#cccccc"><B> User
                                        Statistics </B></TD>
                            </TR>
                            <TR>
                                <TD align="right" valign="top"
                                    width="133"><A
                                    href="docs/glossary.html">account
                                        ID:<BR>
                                </A><A href="docs/glossary.html">account
                                        created:</A><BR> <A
                                    href="docs/glossary.html">total
                                        logins:</A><BR> <A
                                    href="docs/glossary.html">session
                                        created:</A><BR></TD>
                                <TD width="22"></TD>
                                <TD align="left" width="212"><%=accountData.getAccountID()%><BR>
                                    <%=accountData.getCreationDate()%><BR>
                                    <%=accountData.getLoginCount()%><BR>
                                    <%=(java.util.Date) session.getAttribute("sessionCreationDate")%><BR></TD>
                            </TR>
                            <TR>
                                <TD width="133"></TD>
                                <TD width="22"></TD>
                                <TD width="212"></TD>
                            </TR>
                            <TR>
                                <TD colspan="3" bgcolor="#cccccc"><B>Account
                                        Summary </B></TD>
                            </TR>
                            <TR>
                                <TD align="right" valign="top"
                                    width="133"><A
                                    href="docs/glossary.html"> cash
                                        balance:</A><BR> <A
                                    href="docs/glossary.html">number
                                        of holdings:</A><BR> <A
                                    href="docs/glossary.html">total
                                        of holdings:<BR> sum of
                                        cash/holdings<BR> opening
                                        balance:<BR>
                                </A>
                                    <HR></TD>
                                <TD width="22"></TD>
                                <TD align="left" valign="top"
                                    width="212">
                                    <%
                                        BigDecimal openBalance = accountData.getOpenBalance();
                                        BigDecimal balance = accountData.getBalance();
                                        BigDecimal holdingsTotal = FinancialUtils.computeHoldingsTotal(holdingDataBeans);
                                        BigDecimal sumOfCashHoldings = balance.add(holdingsTotal);
                                        BigDecimal gain = FinancialUtils.computeGain(sumOfCashHoldings, openBalance);
                                        BigDecimal gainPercent = FinancialUtils.computeGainPercent(sumOfCashHoldings, openBalance);
                                    %>$ <%=balance%><BR> <%=holdingDataBeans.size()%><BR>
                                    $ <%=holdingsTotal%><BR> $ <%=sumOfCashHoldings%><BR>
                                    $ <%=openBalance%><BR>

                                    <HR>
                                </TD>
                            </TR>
                            <TR>
                                <TD valign="top" align="right"><A
                                    href="docs/glossary.html">current
                                        gain/(loss):</A></TD>
                                <TD></TD>
                                <TD valign="top">$ <B><%=FinancialUtils.printGainHTML(gain)%>
                                        <%=FinancialUtils.printGainPercentHTML(gainPercent)%></B></TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                </TD>
                <TD align="center" valign="top" bgcolor="#ffffff"
                    width="236"><BR>
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
                            Home</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
