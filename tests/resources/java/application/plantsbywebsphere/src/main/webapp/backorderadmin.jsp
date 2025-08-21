<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<!--

COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
developing, using, marketing or distributing application programs conforming to the application 
programming interface for the operating platform for which the sample code is written. 
Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  

(C) COPYRIGHT International Business Machines Corp., 2003,2011
All Rights Reserved * Licensed Materials - Property of IBM

-->
<html>
<head>
<%@ page language="java" contentType="text/html; charset=ISO-8859-1"
	pageEncoding="ISO-8859-1"%>
<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">
<meta name="GENERATOR" content="IBM WebSphere Studio">
<meta http-equiv="Content-Style-Type" content="text/css">
<link href="/PlantsByWebSphere/PlantMain.css" rel="stylesheet"
	type="text/css">
<title>backorderadmin.jsp</title>

<script type="text/javascript" src="/PlantsByWebSphere/applycss.js"
	language="JavaScript"></script>
<script language="JavaScript" src="/PlantsByWebSphere/collectionform.js"></script>
<script>
function verifyFields(theForm)
{
   var result = true;
   var formlen = theForm.length;
   for (var i=0;i<formlen;i++)
   {
      var theitem = theForm.elements[i].name;
      var isselected = theitem.indexOf("selectedObjectIds",0) + 1;
      if (isselected > 0)
      {
         if (theForm.elements[i].checked == true)
         {
            var orderID = theForm.elements[i].value;
            if (verifyQty(theForm, orderID) == false)
            {
               result = false;
               break;
            }
         }
      }
   }
   return result;
}

function verifyQty(theForm, orderID)
{

   var formlen = theForm.length;
   for (var i=0;i<formlen;i++)
   {
      var result = true;
      var theitem = theForm.elements[i].name;       
      var isqty = theitem.indexOf("itemqty" +orderID,0) + 1;
      if (isqty > 0)
      {
         if ((theForm.elements[i].value == "") || (isNaN(parseInt(theForm.elements[i].value))) || (parseInt(theForm.elements[i].value) < 1))
         {
            result = false;
         }
         else
         {
            for (j=0; j < theForm.elements[i].value.length; j++)
            {
               if ( isNaN(parseInt(theForm.elements[i].value.charAt(j))) )
               {
                  result = false;
                  break;
               }
            }
         }
         if (!result) 
         {
            alert("Quantity must be a valid number.");
         }
         return result;
      }
   }
}
</script>

</head>
<body class="adminactions" marginwidth="0" leftmargin="0">

	<%@page
		import="com.ibm.websphere.samples.pbw.war.BackOrderItem,com.ibm.websphere.samples.pbw.jpa.Inventory,com.ibm.websphere.samples.pbw.utils.Util,java.text.SimpleDateFormat,java.util.*"
		session="true" isThreadSafe="true" isErrorPage="false"%>

	<%
		Collection backOrderItems = (Collection) session.getAttribute("backorderitems");
	%>

	<table border="0" cellpadding="4" cellspacing="0" width="100%">
		<tbody>
			<tr>
				<td class="trail">
					<p class="trail">
						<a class="trail" class="footer"
							href="/PlantsByWebSphere/adminactions.html" target="adminactions">Admin
							Home</a>
					</p>
				</td>
			</tr>
			<tr>
				<td width="100%">
					<table cellpadding="5" cellspacing="5" border="0" width="600">
						<tbody>
							<tr>
								<td width="100%" valign="middle">
									<h1>BackOrder Administration</h1>
								</td>
								<td align="right" valign="middle" nowrap></td>
								<td valign="middle"></td>
							</tr>
							<tr>
								<td colspan="2"><font color="#ff0033"> <%
 	String results;
 	results = (String) request.getAttribute(Util.ATTR_RESULTS);
 	if (results != null)
 		out.print(results);
 %>
								</font></td>
							</tr>
							<tr>
								<td colspan="3">
									<p>Here are the inventory items that have been back
										ordered.
									<form method="post"
										action="/PlantsByWebSphere/servlet/AdminServlet">
										<input type="submit" name="GetBackOrders" value="Refresh"
											class="buttons" id="getbackorders"> <input
											type="hidden" name="admintype" value="backorder"> <input
											type="hidden" name="action" value="getbackorders">
									</form>
									</p> <br>
								</td>
							</tr>
							<%
								if (backOrderItems != null) {
							%>
							<tr>
								<td colspan="3">
									<blockquote>
										<b>Back Order Items</b><br>
									</blockquote>
									<p>
										The <b>Back Order Items</b> list shows the inventory items
										that may be ordered from a supplier. Select one or more
										ordered items and click the <b>Order Stock</b> to send an
										order to the supplier. The <b>QUANTITY TO ORDER</b> may be
										changed before the order is submitted.
									</p> <br>
									<table width="524" border="0" cellpadding="2" cellspacing="10">
										<tbody>
											<form onsubmit="return verifyFields(this);" name="order"
												method="post"
												action="/PlantsByWebSphere/servlet/AdminServlet">
												<tr bgcolor="#eeeecc">
													<th><input type="checkbox" name="allchecked"
														value="checkall" ONCLICK="updateCheckAll(this.form)"
														ONKEYPRESS="updateCheckAll(this.form)"></th>
													<th class="item" align="left" nowrap width="150">BACK
														ORDER #</th>
													<th class="item" align="left" nowrap nowrap width="150">ITEM
														#</th>
													<th class="item" align="left" nowrap nowrap width="200">ITEM
														DESCRIPTION</th>
													<th class="item" align="left">QUANTITY TO ORDER</th>
													<th class="item" align="left">CURRENT INVENTORY
														QUANTITY</th>
													<th class="item" align="left">LOW INVENTORY DATE</th>
												</tr>

												<%
													Util.debug("BackOrders Found in backorderadmin.jsp");
														Iterator i = backOrderItems.iterator();
														while (i.hasNext()) {
															BackOrderItem backOrderItem = (BackOrderItem) i.next();
															String status = backOrderItem.getStatus();
															if (status.equals(Util.STATUS_ORDERSTOCK)) {
																String backOrderID = backOrderItem.getBackOrderID();
																String invID = backOrderItem.getInventory().getInventoryId();
																String name = backOrderItem.getName();
																int quantity = backOrderItem.getQuantity();
																int inventoryQuantity = backOrderItem.getInventoryQuantity();

																Date lowDateRaw = new Date(backOrderItem.getLowDate());
																SimpleDateFormat formatter = new SimpleDateFormat("MM/dd/yyyy hh:mm:ss a zzz");
																String lowDate = formatter.format(lowDateRaw);
												%>
												<tr bgcolor="#ffffdd">
													<td valign="top"></td>
													<td valign="top" nowrap>
													<td>
													<td valign="top" nowrap></td>
													<td valign="top" nowrap></td>
													<td valign="top" nowrap></td>
													<td nowrap></td>
													<td></td>
													<td valign="top" nowrap></td>
													<td valign="top" nowrap></td>
												</tr>
												<tr bgcolor="#ffffdd">
													<td valign="top" width="187"><input type="checkbox"
														name="selectedObjectIds" value="<%=backOrderID%>"
														onclick="checkChecks(this.form)"
														onkeypress="checkChecks(this.form)"></td>
													<td valign="top" nowrap width="32">
														<p><%=backOrderID%></p>
													</td>

													<td valign="top" nowrap>
														<p><%=invID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=name%></p>
													</td>
													<td valign="top" nowrap><input type="text"
														name="itemqty<%=backOrderID%>" size="4" maxlength="5"
														value="<%=quantity%>"></td>
													<td valign="top" nowrap>
														<p><%=inventoryQuantity%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=lowDate%></p>
													</td>
												</tr>
												<%
													} // if (status.equals(Utils.STATUS_ORDERSTOCK())
														} // End while (i.hasNext()
													} // if (backOrderItems != null)
													else {
														Util.debug("NO BackOrders Found in backorderadmin.jsp");
													}
												%>
												<tr>
													<td></td>
													<td></td>
													<td nowrap width="132"><input type="hidden"
														name="action" value="orderstock"> <input
														type="submit" name="Order Stock" value="Order Stock"
														class="buttons" id="orderstock"
														onClick='order.action.value="orderstock";return true'>
														<input type="hidden" name="admintype" value="backorder"></td>
													<td nowrap width="32"><input type="submit"
														name="Cancel" value="Cancel" class="buttons" id="cancel"
														onClick='order.action.value="cancel";return true'>
														<input type="hidden" name="admintype" value="backorder"></td>
												</tr>
											</form>
										</tbody>
									</table>
								</td>
							</tr>
							<tr>
								<td colspan="3">
									<blockquote>
										<b>Ordered Items</b><br> <br>
									</blockquote>
									<p>
										The <b>Ordered Items</b> list shows the inventory items that
										have already been ordered from a supplier but have not been
										received yet. Select one or more ordered items and click the <b>Check
											Status</b> to check the status from the supplier.
									</p>
									<table width="600" border="0" cellpadding="2" cellspacing="10">
										<tbody>
											<form name="ordered" method="post"
												action="/PlantsByWebSphere/servlet/AdminServlet">
												<tr bgcolor="#eeeecc">
													<th><input type="checkbox" name="allchecked"
														value="checkall" ONCLICK="updateCheckAll(this.form)"
														ONKEYPRESS="updateCheckAll(this.form)"></th>
													<th class="item" align="left" nowrap width="150">BACK
														ORDER #</th>
													<th class="item" align="left" nowrap width="150">SUPPLIER
														ORDER #</th>
													<th class="item" align="left" nowrap width="150">ITEM
														#</th>
													<th class="item" align="left" nowrap width="200">ITEM
														DESCRIPTION</th>
													<th class="item" align="left">QUANTITY ORDERED</th>
													<th class="item" align="left">CURRENT INVENTORY
														QUANTITY</th>
													<th class="item" align="left">LOW INVENTORY DATE</th>
													<th class="item" align="left">ORDERED DATE</th>
												</tr>
												<%
													if (backOrderItems != null) {
														Util.debug("BackOrders Found in backorderadmin.jsp");
														Iterator i = backOrderItems.iterator();
														while (i.hasNext()) {
															BackOrderItem backOrderItem = (BackOrderItem) i.next();
															String status = backOrderItem.getStatus();
															if (status.equals(Util.STATUS_ORDEREDSTOCK)) {
																String backOrderID = backOrderItem.getBackOrderID();
																String supplierOrderID = backOrderItem.getSupplierOrderID();
																String invID = backOrderItem.getInventory().getInventoryId();
																String name = backOrderItem.getName();
																int quantity = backOrderItem.getQuantity();
																int inventoryQuantity = backOrderItem.getInventoryQuantity();

																Date lowDateRaw = new Date(backOrderItem.getLowDate());
																Date orderedDateRaw = new Date(backOrderItem.getOrderDate());

																SimpleDateFormat formatter = new SimpleDateFormat("MM/dd/yyyy hh:mm:ss a zzz");
																String lowDate = formatter.format(lowDateRaw);
																String orderedDate = formatter.format(orderedDateRaw);
												%>
												<tr bgcolor="#ffffdd">
													<td valign="top"><input type="checkbox"
														name="selectedObjectIds" value="<%=backOrderID%>"
														onclick="checkChecks(this.form)"
														onkeypress="checkChecks(this.form)"></td>
													<td valign="top" nowrap>
														<p><%=backOrderID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=supplierOrderID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=invID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=name%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=quantity%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=inventoryQuantity%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=lowDate%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=orderedDate%></p>
													</td>
												</tr>
												<%
													} // if (status.equals(Utils.STATUS_ORDEREDSTOCK())
														} // End while (i.hasNext()
													} // if (backOrderItems != null)
													else {
														Util.debug("NO BackOrders Found in backorderadmin.jsp");
													}
												%>
												<tr>
													<td></td>
													<td nowrap width="132"><input type="hidden"
														name="action" value="orderstatus"> <input
														type="submit" name="Order Status" value="Order Status"
														class="buttons" id="orderstatus"
														onClick='order.action.value="orderstatus";return true'>
														<input type="hidden" name="admintype" value="backorder"></td>
													<td nowrap width="32"><input type="submit"
														name="Cancel" value="Cancel" class="buttons" id="cancel">
														<input type="hidden" name="admintype" value="backorder">
														<input type="hidden" name="action" value="cancel"></td>
												</tr>
											</form>
										</tbody>
									</table>
								</td>
							</tr>
							<tr>
								<td colspan="3">
									<blockquote>
										<b>Received Items</b><br> <br>
									</blockquote>
									<p>
										The <b>Received Items</b> list shows the inventory items that
										have been received from a supplier but have not been added to
										the inventory. Select one or more ordered items and click the
										<b>Update Stock</b> to add the inventory received from the
										supplier.
									</p>
									<table width="600" border="0" cellpadding="2" cellspacing="10">
										<tbody>
											<form name="received" method="post"
												action="/PlantsByWebSphere/servlet/AdminServlet">
												<tr bgcolor="#eeeecc">
													<th><input type="checkbox" name="allchecked"
														value="checkall" ONCLICK="updateCheckAll(this.form)"
														ONKEYPRESS="updateCheckAll(this.form)"></th>
													<th class="item" align="left" nowrap width="150">BACK
														ORDER #</th>
													<th class="item" align="left" nowrap width="150">SUPPLIER
														ORDER #</th>
													<th class="item" align="left" nowrap width="150">ITEM
														#</th>
													<th class="item" align="left" nowrap width="200">ITEM
														DESCRIPTION</th>
													<th class="item" align="left">QUANTITY RECEIVED</th>
													<th class="item" align="left">CURRENT INVENTORY
														QUANTITY</th>
													<th class="item" align="left">LOW INVENTORY DATE</th>
													<th class="item" align="left">ORDERED DATE</th>
												</tr>
												<%
													if (backOrderItems != null) {
														Util.debug("BackOrders Found in backorderadmin.jsp");
														Iterator i = backOrderItems.iterator();
														while (i.hasNext()) {
															BackOrderItem backOrderItem = (BackOrderItem) i.next();
															String status = backOrderItem.getStatus();
															if (status.equals(Util.STATUS_RECEIVEDSTOCK)) {
																String backOrderID = backOrderItem.getBackOrderID();
																String supplierOrderID = backOrderItem.getSupplierOrderID();
																String invID = backOrderItem.getInventory().getInventoryId();
																String name = backOrderItem.getName();
																int quantity = backOrderItem.getQuantity();
																int inventoryQuantity = backOrderItem.getInventoryQuantity();

																Date lowDateRaw = new Date(backOrderItem.getLowDate());
																Date orderedDateRaw = new Date(backOrderItem.getOrderDate());

																SimpleDateFormat formatter = new SimpleDateFormat("MM/dd/yyyy hh:mm:ss a zzz");
																String lowDate = formatter.format(lowDateRaw);
																String orderedDate = formatter.format(orderedDateRaw);
												%>
												<tr bgcolor="#ffffdd">
													<td valign="top"><input type="checkbox"
														name="selectedObjectIds" value="<%=backOrderID%>"
														onclick="checkChecks(this.form)"
														onkeypress="checkChecks(this.form)"></td>
													<td valign="top" nowrap>
														<p><%=backOrderID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=supplierOrderID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=invID%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=name%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=quantity%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=inventoryQuantity%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=lowDate%></p>
													</td>
													<td valign="top" nowrap>
														<p><%=orderedDate%></p>
													</td>
												</tr>

												<%
													} // if (status.equals(Util.STATUS_RECEIVEDSTOCK))
														} // End while (i.hasNext()
													} // if (backOrderItems != null)
													else {
														Util.debug("NO BackOrders Found in backorderadmin.jsp");
													}
												%>
												<tr>
													<td></td>
													<td></td>
													<td nowrap width="132"><input type="hidden"
														name="action" value="updatestock"> <input
														type="submit" name="Update Stock" value="Update Stock"
														class="buttons" id="updatestock"
														onClick='received.action.value="updatestock";return true'>
														<input type="hidden" name="admintype" value="backorder"></td>
													<td nowrap width="32"><input type="submit"
														name="Cancel" value="Cancel" class="buttons" id="cancel"
														onClick='received.action.value="cancel";return true'>
														<input type="hidden" name="admintype" value="backorder"></td>
												</tr>
											</form>
										</tbody>
									</table>
								</td>
							</tr>
						</tbody>
					</table> <br>
				</td>
			</tr>
		</tbody>
	</table>
	<table bgcolor="#669966" border="0" cellpadding="0" cellspacing="0"
		width="100%">
		<tbody>
			<tr>
				<td width="1"><img border="0"
					src="resources/images/1x1_trans.gif" width="1" height="1" alt=""></td>
			</tr>
		</tbody>
	</table>
	<p>
		<br>
	</p>
	<table border="0" cellpadding="5" cellspacing="0" width="100%">
		<tr>
			<td><img src="resources/images/poweredby_WebSphere.gif"
				alt="Powered by WebSphere"></td>
			<td>
				<p class="footer">
					<a class="footer" href="/PlantsByWebSphere/index.html"
						target="_top">Home</a>&nbsp;&nbsp;:&nbsp; <a class="footer"
						href="/PlantsByWebSphere/admin.html" target="_top">Admin Home</a>&nbsp;&nbsp;:&nbsp;
					<a class="footer" href="help.jsf" target="_blank">Help</a>
				</p>
			</td>
		</tr>
	</table>
</body>
</html>
