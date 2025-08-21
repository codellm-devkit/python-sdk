<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
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
<HTML>
<HEAD>
<META http-equiv="Content-Style-Type" content="text/css">
<TITLE>DayTrader: Quotes and Trading</TITLE>
<LINK rel="stylesheet" href="style.css" type="text/css" />
<link rel="shortcut icon" href="./favicon.ico" />
</HEAD>

<BODY bgcolor="#ffffff" link="#000099" vlink="#000099">
    <%@ page
        import="java.util.Collection,java.math.BigDecimal,com.ibm.websphere.samples.daytrader.util.Log,
                java.util.Iterator,com.ibm.websphere.samples.daytrader.entities.OrderDataBean,com.ibm.websphere.samples.daytrader.entities.QuoteDataBean,com.ibm.websphere.samples.daytrader.util.FinancialUtils"
        session="true" isThreadSafe="true" isErrorPage="false"%>
         <jsp:useBean id="quoteDataBeans" type="java.util.Collection<?>"
        scope="request" />

    <TABLE height="54">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10" colspan=5><B><FONT
                        color="#ffffff">DayTrader Quotes</FONT></B></TD>
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
                    border="0" alt=""><BR> <FONT
                    color="#ff0000" size="-2"><%=new java.util.Date()%></FONT></TD>
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
    <TABLE width="650">
        <TBODY>
            <TR>
                <TD>
                    <TABLE width="100%">
                        <TBODY>
                            <TR>
                                <TD></TD>
                            </TR>
                            <TR>
                                <TD bgcolor="#cccccc"><B>Quotes</B></TD>
                            </TR>
                            <TR>
                                <TD align="center">
                                    <TABLE border="1"
                                        style="font-size: smaller">
                                        <TBODY>
                                            <TR align="center">
                                                <TD><A
                                                    href="docs/glossary.html">symbol</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">company</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">volume</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">price
                                                        range</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">open
                                                        price</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">current
                                                        price</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">gain/(loss)</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">trade</A></TD>
                                            </TR>
     </TR>
                                            <%
                                                 // Create Hashmap for quick lookup of quote values
                                                Iterator<?> it = quoteDataBeans.iterator();
                                                while (it.hasNext()) {
                                                try {
                                                    QuoteDataBean quoteData = (QuoteDataBean) it.next();
                                            %>
                                            <TR align="center" bgcolor="#fafcb6">
    <TD><%=FinancialUtils.printQuoteLink(quoteData.getSymbol())%></TD>
    <TD><%=quoteData.getCompanyName()%></TD>
    <TD><%=quoteData.getVolume()%></TD>
    <TD><%=quoteData.getLow() + " - " + quoteData.getHigh()%></TD>
    <TD nowrap><%=quoteData.getOpen()%></TD>
    <TD>$ <%=quoteData.getPrice()%></TD>
    <TD><%=FinancialUtils.printGainHTML(new BigDecimal(quoteData.getChange()))%>
        <%=FinancialUtils.printGainPercentHTML(FinancialUtils.computeGainPercent(quoteData.getPrice(), quoteData.getOpen()))%></TD>
    <TD>
        <FORM action="">
            <INPUT type="submit" name="action" value="buy">
            <INPUT type="hidden" name="symbol" value="<%=quoteData.getSymbol()%>">
            <INPUT size="4" type="text" name="quantity" value="100">
        </FORM>
    </TD>
</TR>

<%
    } catch (Exception e) { 
        Log.error("displayQuote.jsp exception. Check that symbol: exists in the database.", e); 
    } 
%>
                                            

                                            <%
                                                }
                                            %>
                                        </TBODY>
                                    </TABLE>
                                </TD>
                            </TR>
                        </TBODY>
                    </TABLE>
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
                    height="10" colspan=5><B><FONT
                        color="#ffffff">DayTrader Quotes</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR>
                <TD colspan="2" align="center"><IMG
                    src="images/ticker-anim.gif" width="385" height="22"
                    border="0" align="middle" alt="">
                <TD></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
