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
<TITLE>DayTrader Market Summary</TITLE>
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
    
    <TABLE height="54">
        <TBODY>
            <TR style="font-size:12pt;">
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10" colspan=6><B><FONT
                        color="#ffffff">DayTrader Market Summary</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
             <TR style="font-size:12pt;" align="left">
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
        <jsp:include page="marketSummary.html" />
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
                            Market Summary</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
